import os
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ['CUDA_VISIBLE_DEVICES'] = '0'
from models.model_direct import get_model_att_with_sequence_logits
from models.dataloder_direct import MySource_seqs
import jax
import jax.numpy as jnp
from flax import nnx
from flax import linen as nn
from flax.nnx import bridge
import pickle
import optax
import numpy as np
import pandas as pd
import itertools
from scipy.stats import spearmanr
import pickle
from models.cv_dataset import create_cv_datasets
from models.save_fig import evaluate_with_recall,evaluate_with_spearman
import json

class EarlyStopping:
    def __init__(self, patience=10, min_delta=0.001, restore_best_weights=True):
        self.patience = patience
        self.min_delta = min_delta
        self.restore_best_weights = restore_best_weights
        self.best_corr = -1.0
        self.wait = 0
        self.stopped_epoch = 0
        self.best_weights = None
        
    def __call__(self, current_corr, model=None):

        if current_corr - self.best_corr > self.min_delta:
            self.best_corr = current_corr
            self.wait = 0
            if self.restore_best_weights and model is not None:
                _, self.best_weights = nnx.split(model)
        else:
            self.wait += 1
            
        if self.wait >= self.patience:
            self.stopped_epoch = True
            return True
        return False
    
    def restore_weights(self, model):
        if self.restore_best_weights and self.best_weights is not None:
            graph, _ = nnx.split(model)
            return nnx.merge(graph, self.best_weights)
        return model

@nnx.jit
def train_step(model_new, optimizer, x1, y):
    def loss_fn(model_new):
        y_pred = model_new(x1)['global']
        loss = jnp.mean((y - y_pred) ** 2)
        return loss
    grads: nnx.State = nnx.grad(loss_fn)(model_new)
    optimizer.update(grads)
    #return loss_fn(model)

@nnx.jit
def test_step(model_new, x1, y):
    y_pred = model_new(x1)['global']
    loss = jnp.mean((y - y_pred) ** 2)
    return y_pred,loss

tables = pd.read_csv('../data/DMS_substitutions-test.csv')
cv_schemes = ['fold_random_5','fold_modulo_5', 'fold_contiguous_5']
seed = 42

for i in range(len(tables)):
    file_name = tables.iloc[i]['DMS_id']
    table = f'../data/{file_name}.csv'
    y_name = 'DMS_score'

    source_zero = MySource_seqs(table, y_name)

    for cv_scheme in cv_schemes:
        cv_datasets = create_cv_datasets(
            source_zero=source_zero,
            cv_scheme=cv_scheme,
            seed=seed,
            batch_size_train=64,
            batch_size_test=128
        )

        all_fold_results = {}

        for fold_data in cv_datasets:
            fold_num = fold_data['fold']
            train_dataset = fold_data['train_dataset']
            test_dataset = fold_data['test_dataset']


            batch_size, sequence_length = 16, len(source_zero[0]['sequence'])
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

            with open(f'../pretrain_weight/pretrain-{file_name}-random.pkl', 'rb') as f:
                loaded_full_params = pickle.load(f)
            loaded_student_params = jax.device_put(loaded_full_params)

            model_new = nnx.merge(graph, loaded_student_params)


            optimizer = nnx.Optimizer(model_new, optax.adam(1e-4))

            early_stopping = EarlyStopping(
                patience=15,
                min_delta=0.002,
                restore_best_weights=True
            )
            
            train_history = {
                'epoch': [],
                'train_loss': [],
                'test_corr': [],
                'best_corr': []
            }
            
            epochs = 130
            for epoch in range(epochs):
                outs = []
                y_predict=[]
                y_lable=[]
                for data in train_dataset:
                    x1 = data['sequence_onehot']
                    y = data['y']
                    train_step(model_new, optimizer, x1, y)
                    train_pred, out = test_step(model_new, x1, y)
                    outs.append(out)
                for data_test in test_dataset:
                    x1_test = data_test['sequence_onehot']
                    y_test  = data_test['y']
                    out_test_pred,out_test_loss = test_step(model_new, x1_test, y_test)
                    y_predict.append(out_test_pred)
                    y_lable.append(y_test)
                y_predict = list(itertools.chain.from_iterable(y_predict))
                y_lable = list(itertools.chain.from_iterable(y_lable))
                y_predict_plt=jnp.array(y_predict)
                y_lable_plt=jnp.array(y_lable)
                y0 = pd.DataFrame(jnp.squeeze(y_predict_plt))
                y1 = pd.DataFrame(jnp.squeeze(y_lable_plt))
                corr, p = spearmanr(y0,y1)
                
                avg_train_loss = sum(outs) / len(outs)
                
                train_history['epoch'].append(epoch)
                train_history['train_loss'].append(float(avg_train_loss))
                train_history['test_corr'].append(float(corr))
                train_history['best_corr'].append(float(early_stopping.best_corr))
                
                print(f'Epoch: {epoch:3d}, Loss: {avg_train_loss:.4f}, Test_Corr: {corr:.4f}, Best_Corr: {early_stopping.best_corr:.4f}')
                print(f'File: {file_name}, Wait: {early_stopping.wait}/{early_stopping.patience}')
                
                if early_stopping(corr, model_new):
                    print(f"早停触发！在第 {epoch} 个epoch停止训练")
                    print(f"最佳相关系数: {early_stopping.best_corr:.4f}")
                    model_new = early_stopping.restore_weights(model_new)
                    break
                    
                if corr > 0.98:
                    print(f"相关系数达到阈值 0.98，在第 {epoch} 个epoch停止训练")
                    break

            history_df = pd.DataFrame(train_history)
            # history_df.to_csv(f'../training_history/finetune-{file_name}-{cv_scheme}-history.csv', index=False)
            
            graph, state = nnx.split(model_new)
            with open(f'../finetune_weight/finetune-{fold_num}-{file_name}-{cv_scheme}.pkl', 'wb') as f:
                pickle.dump(state, f)
                
            print(f"训练完成！文件: {file_name}")
            print(f"最终相关系数: {corr:.4f}")
            print(f"总训练轮数: {epoch + 1}")
            print("-" * 50)
            recall_result = evaluate_with_recall(model_new, test_dataset,threshold_percentiles=[70, 80, 90, 95])
            spearman_result, mse_result = evaluate_with_spearman(model_new,test_dataset)
    
            all_results = {
            'recall_results': recall_result,
            'spearman_result': spearman_result,
            'mse_result': mse_result}
            all_fold_results [f'fold_{fold_num}'] = {'results': all_results}
            with open(f'../result/{file_name}-finetune-{cv_scheme}-random.json', 'w') as f:
                json.dump(all_fold_results, f, indent=2)
            
            
            
            
