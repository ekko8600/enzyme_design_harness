import os
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ['CUDA_VISIBLE_DEVICES'] = '3'
os.environ["XLA_PYTHON_CLIENT_PREALLOCATE"] = "false"

from models.model_direct import get_model_att_with_sequence_logits
from models.dataloder_dpo_direct_3 import MySourceWithSequences,MySourceSubset
from models.cv_dataset import create_varying_size_datasets
from models.pair_dataset import PreferenceDataGenerator
from models.trainer import SequenceDPOTrainer
from models.save_fig import plot_training_curves,evaluate_with_recall,evaluate_with_spearman

import jax
from flax import nnx
from flax.nnx import bridge
import pickle
import json
import pandas as pd

def train_dpo_model(train_s, test_s, test_d):

    train_subset = MySourceSubset(source_zero, train_s)
    test_subset = MySourceSubset(source_zero, test_s)
    train_generator = PreferenceDataGenerator(train_subset)
    eval_generator = PreferenceDataGenerator(test_subset)
    train_preference_pairs = train_generator.create_preference_pairs(n_pairs= max(len(train_subset)*2,10000), strategy='threshold', percentile=90)
    eval_preference_pairs = eval_generator.create_preference_pairs(n_pairs= max(len(test_subset),10000), strategy='threshold', percentile=90)
    
    trainer = SequenceDPOTrainer(
        model=model_new,
        beta=0.1,
        global_loss_weight=0.3,     # 0.1，0.3，0.5，0.8，1 (小样本建议0.3-0.5)
        learning_rate=2e-6,         # 小样本场景建议 1e-6 ~ 2e-6
        warmup_epochs=3,            # warmup期间学习率线性增长，小样本建议3-5
        total_epochs=80,            # 用于curriculum learning的总epoch数
        seed=42
    )

    trainer.train(train_generator, 
                num_epochs=150, 
                patience=8,         # 连续5个epoch无改善就停止
                min_delta=0.002,    # 最小改善阈值
                monitor_metric='total_loss')  # 监控指标：total_loss最全面
    
    plot_training_curves(trainer.training_stats)
    recall_result = evaluate_with_recall(model_new, test_d,threshold_percentiles=[70, 80, 90, 95])
    spearman_result, mse_result = evaluate_with_spearman(model_new,test_d)
    
    all_results = {
    'recall_results': recall_result,
    'spearman_result': spearman_result,
    'mse_result': mse_result}
    print("DPO训练完成！")
    return all_results

table_file = pd.read_csv('../proteingym/DMS_substitutions-low.csv')

for i in range(len(table_file)):
    table_name = table_file.iloc[i]['DMS_id']
    pdb_name = table_file.iloc[i]['pdb_file']

    table = f'../proteingym/DMS_ProteinGym_substitutions/{table_name}.csv'
    pdb_path = f"../proteingym/ProteinGym_AF2_structures/{pdb_name}.pdb"


    y_name = 'DMS_score'
    source_zero = MySourceWithSequences(table, y_name,table_name,pdb_path)

    batch_size, sequence_length = 16, len(source_zero[0]['sequence'])
    input_dim1 = sequence_length + 1
    input_dim2 = 1152
    input_dim3 = 1280
    vocab_size = 20

    key1, key2, key3 = jax.random.split(jax.random.PRNGKey(42), 3)
    X1 = jax.random.randint(key1, (batch_size, sequence_length, vocab_size), 0, 1)

    student_model = bridge.ToNNX(get_model_att_with_sequence_logits(
        sequence_length=sequence_length,
        vocab_size=vocab_size,
        task_type='both',
        use_temperature=True,
        use_sequence_embedding=True,
        embed_dim=512
    ), rngs=nnx.Rngs(42))

    student_model = bridge.lazy_init(student_model, X1)

    graph, state_0 = nnx.split(student_model)

    with open(f'../proteingym/pretrain_weight/direct-SFT-pretrain-{table_name}-multi.pkl', 'rb') as f:
        loaded_full_params = pickle.load(f)
    loaded_student_params = jax.device_put(loaded_full_params)

    model_new = nnx.merge(graph, loaded_student_params)

    # # 使用默认参数
    # datasets = create_varying_size_datasets(source_zero)

    datasets = create_varying_size_datasets(
        source_zero, 
        train_sizes=[20,40,60,80,100],
        random_seeds=[10,20,30,40,50,60,70,80,90,100],
        batch_size_train=32,
        batch_size_test=64
    )

    all_fold_results = {}

    for fold_data in datasets:
        fold_num = fold_data['train_size']
        seed = fold_data['seed']
        train_dataset = fold_data['train_dataset']
        test_dataset = fold_data['test_dataset']
        train_source = fold_data['train_indices']
        test_source = fold_data['test_indices']

        
        results = train_dpo_model(train_source, test_source, test_dataset)
        
        all_fold_results[f'train_{fold_num}_seed_{seed}'] = {'results': results}
        
    with open(f'./result/eval_result_percentile_90_lown/{table_name}-DPO_lown.json', 'w') as f:
        json.dump(all_fold_results, f, indent=2)
    
