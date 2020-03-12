python ./t2t_bert/distributed_bin/hvd_train_eval_api_v1.py \
 --buckets "/data/xuht" \
 --config_file "./data/textcnn/textcnn.json" \
 --init_checkpoint "" \
 --vocab_file "./data/chinese_L-12_H-768_A-12/vocab.txt" \
 --label_id "./data/lcqmc/label_dict.json" \
 --max_length 128 \
 --train_file "chenxi/disu/distillation/data/train_tfrecords" \
 --dev_file " chenxi/disu/distillation/data/dev_tfrecords" \
 --model_output "henxi/disu/distillation/data/textcnn_distillation" \
 --epoch 100 \
 --num_classes 2 \
 --train_size 1301515 \
 --eval_size 144680 \
 --batch_size 32 \
 --model_type "textcnn" \
 --if_shard 1 \
 --is_debug 1 \
 --run_type "sess" \
 --opt_type "hvd" \
 --num_gpus 4 \
 --parse_type "parse_batch" \
 --rule_model "normal" \
 --profiler "no" \
 --train_op "adam" \
 --running_type "train" \
 --cross_tower_ops_type "paisoar" \
 --distribution_strategy "MirroredStrategy" \
 --load_pretrained "no" \
 --w2v_path "chinese_L-12_H-768_A-12/vocab_w2v.txt" \
 --with_char "no_char" \
 --input_target "a" \
 --decay "no" \
 --warmup "no" \
 --distillation "distillation" \
 --temperature 2.0 \
 --distillation_ratio 0.5 \
 --task_type "single_sentence_classification" \
 --classifier order_classifier


