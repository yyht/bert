from utils.bert import bert_utils
from utils.bert import bert_modules

import numpy as np

import collections
import copy
import json
import math
import re
import six
import tensorflow as tf
from loss import loss_utils

try:
  from utils.contrastive_utils import add_contrastive_loss as simlr_contrastive_loss_fn
except:
  simlr_contrastive_loss_fn = None

"""
https://github.com/google-research/mixmatch/blob/master/mixup.py
https://github.com/google-research/simclr/blob/master/model.py
"""

def linear_layer(x,
                 is_training,
                 num_classes,
                 use_bias=True,
                 use_bn=False,
                 name='linear_layer'):
  """Linear head for linear evaluation.
  Args:
    x: hidden state tensor of shape (bsz, dim).
    is_training: boolean indicator for training or test.
    num_classes: number of classes.
    use_bias: whether or not to use bias.
    use_bn: whether or not to use BN for output units.
    name: the name for variable scope.
  Returns:
    logits of shape (bsz, num_classes)
  """
  assert x.shape.ndims == 2, x.shape
  with tf.variable_scope(name, reuse=tf.AUTO_REUSE):
    x = tf.layers.dense(
        inputs=x,
        units=num_classes,
        use_bias=use_bias and not use_bn,
        kernel_initializer=tf.random_normal_initializer(stddev=.01))
    if use_bn:
      x = resnet.batch_norm_relu(x, is_training, relu=False, center=use_bias)
    x = tf.identity(x, '%s_out' % name)
  return x

def projection_head(FLAGS, hiddens, is_training, 
                use_bn=False,
                name='head_contrastive'):
  """Head for projecting hiddens fo contrastive loss."""
  with tf.variable_scope(name, reuse=tf.AUTO_REUSE):
    mid_dim = hiddens.shape[-1]
    out_dim = FLAGS.proj_out_dim
    hiddens_list = [hiddens]
    if FLAGS.proj_head_mode == 'none':
      pass  # directly use the output hiddens as hiddens.
    elif FLAGS.proj_head_mode == 'linear':
      hiddens = linear_layer(
          hiddens, is_training, out_dim,
          use_bias=False, use_bn=use_bn, name='l_0')
      hiddens_list.append(hiddens)
    elif FLAGS.proj_head_mode == 'nonlinear':
      for j in range(FLAGS.num_proj_layers):
        if j != FLAGS.num_proj_layers - 1:
          # for the middle layers, use bias and relu for the output.
          dim, bias_relu = mid_dim, True
        else:
          # for the final layer, neither bias nor relu is used.
          dim, bias_relu = FLAGS.proj_out_dim, False
        hiddens = linear_layer(
            hiddens, is_training, dim,
            use_bias=bias_relu, use_bn=use_bn, name='nl_%d'%j)
        hiddens = tf.nn.relu(hiddens) if bias_relu else hiddens
        hiddens_list.append(hiddens)
    else:
      raise ValueError('Unknown head projection mode {}'.format(
          FLAGS.proj_head_mode))
    # take the projection head output during pre-training.
    hiddens = hiddens_list[-1]
  return hiddens

def _sample_from_softmax(logits, disallow=None):
  if disallow is not None:
    logits -= 1000.0 * disallow
  uniform_noise = tf.random.uniform(
     get_shape_list(logits), minval=0, maxval=1)
  gumbel_noise = -tf.log(-tf.log(uniform_noise + 1e-9) + 1e-9)
  return tf.one_hot(tf.argmax(tf.nn.softmax(logits + gumbel_noise), -1,
                              output_type=tf.int32), logits.shape[-1])

def _sample_positive(features, batch_size):

  logits = tf.log(1./tf.ones((batch_size, batch_size), dtype=tf.float32)/batch_size)
  # [batch_size, batch_size]
  disallow_mask = tf.eye((batch_size))

  sampled_onthot = _sample_from_softmax(logits, disallow_mask)
  positive_ids = tf.argmax(sampled_onthot, axis=-1, output_type=tf.int32)
  sampled_feature = tf.gather_nd(features, positive_ids[:, None])
  return sampled_feature, positive_ids

def my_contrastive_loss(hidden,
                     hidden_norm=True,
                     temperature=1.0,
                     tpu_context=None,
                     weights=1.0):

    if hidden_norm:
      hidden = tf.nn.l2_normalize(hidden, -1)
    hidden1, hidden2 = tf.split(hidden, 2, 0)

    batch_size = bert_utils.get_shape_list(hidden, expected_rank=[2,3])[0]

    hidden1_large = hidden1
    hidden2_large = hidden2
    labels = tf.one_hot(tf.range(batch_size), batch_size * 2)
    masks = tf.one_hot(tf.range(batch_size), batch_size)

    logits_aa = tf.matmul(hidden1, hidden1_large, transpose_b=True) / temperature
    logits_aa = logits_aa - masks * LARGE_NUM
    logits_bb = tf.matmul(hidden2, hidden2_large, transpose_b=True) / temperature
    logits_bb = logits_bb - masks * LARGE_NUM
    logits_ab = tf.matmul(hidden1, hidden2_large, transpose_b=True) / temperature
    logits_ba = tf.matmul(hidden2, hidden1_large, transpose_b=True) / temperature

    loss_a = tf.losses.softmax_cross_entropy(
      labels, tf.concat([logits_ab, logits_aa], 1), weights=weights)
    loss_b = tf.losses.softmax_cross_entropy(
        labels, tf.concat([logits_ba, logits_bb], 1), weights=weights)
    loss = loss_a + loss_b
    return loss, logits_ab, labels

def random_mixup(hidden, sampled_hidden, beta=0.5):

    hidden_shape_list = bert_utils.get_shape_list(hidden, expected_rank=[2,3])
    batch_size = hidden_shape_list[0]
    
    mix = tf.distributions.Beta(beta, beta).sample([batch_size, 1])
    mix = tf.maximum(mix, 1 - mix)

    xmix_linear = hidden * mix + sampled_hidden * (1.0 - mix)
    xmix_geometric = tf.pow(hidden, mix) * tf.pow(sampled_hidden, (1.0 - mix))

    binary_noise_dist = tf.distributions.Bernoulli(probs=mix * tf.ones_like(hidden), 
                                                dtype=tf.float32)
    binary_mask = binary_noise_dist.sample()
    binary_mask = tf.cast(binary_mask, tf.float32)
    xmix_binary = hidden * binary_mask +  sampled_hidden * (1.0 - binary_mask)

    mixup_noise_sample = [xmix_linear, xmix_geometric, xmix_binary]
    # [batch_size, len(mixup_noise_sample), hidden_dims]
    mixup_matrix = tf.stack(mixup_noise_sample, axis=1)

    mixup_matrix_shape = bert_utils.get_shape_list(mixup_matrix, expected_rank=[2,3])

    batch_size = mixup_matrix_shape[0]
    noise_num = mixup_matrix_shape[1]

    sample_prob = tf.ones((batch_size, noise_num), dtype=tf.float32)/noise_num
    mixup_noise_idx = tf.multinomial(tf.log(sample_prob)+1e-10,
              num_samples=1,
              output_dtype=tf.int32) # batch x 1

    batch_idx = tf.expand_dims(tf.cast(tf.range(batch_size), tf.int32), axis=-1)
    gather_index = tf.concat([batch_idx, mixup_noise_idx], axis=-1)
    mixup_noise = tf.gather_nd(mixup_matrix, gather_index)
    return mixup_noise

def mixup_dsal_plus(config, 
        hidden,
        input_mask,
        temperature=0.1,
        hidden_norm=True,
        masked_repres=None,
        is_training=True,
        beta=0.5,
        use_bn=True,
        tpu_context=None,
        weights=1.0):
    input_shape_list = bert_utils.get_shape_list(hidden, expected_rank=3)
    batch_size = input_shape_list[0]
    seq_length = input_shape_list[1]
    hidden_dims = input_shape_list[2]

    hidden_mask = tf.cast(input_mask[:, None], dtype=tf.float32)
    mean_pooling = tf.reduce_sum(hidden_mask*hidden, axis=1)
    mean_pooling /= tf.reduce_sum(hidden_mask, axis=1)

    # [batch_size, hidden_dims]
    positive_1_repres = _sample_positive(mean_pooling, batch_size)
    xmix_a = random_mixup(mean_pooling, positive_1_repres, beta=beta)
    
    positive_2_repres = _sample_positive(mean_pooling, batch_size)
    xmix_b = random_mixup(mean_pooling, positive_2_repres, beta=beta)

    xmix_features = tf.concat([xmix_a, xmix_b], 0)  # (num_transforms * bsz, h, w, c)

    with tf.variable_scope('cls/simclr_projection_head', reuse=tf.AUTO_REUSE):
      xmix_hiddens = projection_head(config, 
                              xmix_features, 
                              is_training, 
                              name='head_contrastive')

    # [2*batch_size, hidden_dims]
    if simlr_contrastive_loss_fn:
      contrastive_loss_fn = simlr_contrastive_loss_fn
      tf.logging.info("== apply tpu-simclr cross batch ==")
    else:
      contrastive_loss_fn = my_contrastive_loss
      tf.logging.info("== apply simclr local batch ==")
    [loss, logits_ab, labels] = contrastive_loss_fn(xmix_hiddens,
                     hidden_norm=hidden_norm,
                     temperature=temperature,
                     tpu_context=tpu_context,
                     weights=weights)
    return loss