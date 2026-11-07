import os
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ['CUDA_VISIBLE_DEVICES'] = '0'

from models.model_direct import get_model_att_with_sequence_logits
from models.dataloder_direct_pretrain import MySource_seqs
import grain.python as grain
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
def train_step_mixed(model_new, optimizer, x1, y_list):
    def loss_fn(model_new):
        y_pred = model_new(x1)['global']
        total_loss = 0.0
        for y in y_list:
            total_loss += jnp.mean((y - y_pred) ** 2)
        return total_loss
    grads: nnx.State = nnx.grad(loss_fn)(model_new)
    optimizer.update(grads)

@nnx.jit
def test_step(model_new, x1, y):
    y_pred = model_new(x1)['global']
    loss = jnp.mean((y - y_pred) ** 2)
    return y_pred,loss

@nnx.jit
def test_step_multi(model_new, x1, y_list):
    y_pred = model_new(x1)['global']
    losses = []
    for y in y_list:
        loss = jnp.mean((y - y_pred) ** 2)
        losses.append(loss)
    return y_pred, losses



# ========== 用户配置参数 ==========
tables = pd.read_csv('../data/DMS_substitutions-test.csv')

y_names = ['gemme_fitness']  # 可选: 'saprot_fitness', 'prosst_fitness', 'esmif_fitness', 's3f_fitness', 'poet_fitness'等

# 训练模式
# 'single': 单一目标训练（仅使用y_names中的第一个）
# 'mixed': 混合蒸馏模式（所有y_name的MSE损失之和）
# 'random': 随机蒸馏模式（每个epoch随机选择一个y_name）
train_mode = 'random'  # 可选: 'single', 'mixed', 'random'
# ==================================

for i in range(len(tables)):
    file_name = tables.iloc[i]['DMS_id']
    table = f'../data/{file_name}-pretrain.csv'
    
    if train_mode == 'single':
        y_name = y_names[0]
        print(f"训练模式: 单一目标训练，使用 {y_name}")
    elif train_mode in ['mixed', 'random']:
        y_name = y_names
        print(f"训练模式: {train_mode}，使用 {y_names}")
    else:
        raise ValueError(f"未知的训练模式: {train_mode}，请选择 'single', 'mixed' 或 'random'")

    source_zero = MySource_seqs(table, y_name)
    train_dataset = (grain.MapDataset.source(source_zero)
            .shuffle(seed=42)
            .slice(slice(0, int(len(source_zero)*0.9)))
            .batch(batch_size=64)
            )

    test_dataset = (grain.MapDataset.source(source_zero)
            .shuffle(seed=42)
            .slice(slice(int(len(source_zero)*0.9),int(len(source_zero)+1)))
            .batch(batch_size=128)
            )

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

    model_new = bridge.lazy_init(student_model, X1)
    reference_model = bridge.lazy_init(student_model, X1)


    optimizer = nnx.Optimizer(model_new, optax.adam(1e-4))
    
    early_stopping = EarlyStopping(
                patience=10,
                min_delta=0.002,
                restore_best_weights=True
            )

    epochs = 20
    for epoch in range(epochs):
        outs = []
        y_predict=[]
        y_lable=[]
        
        if train_mode == 'random':
            selected_y_idx = np.random.randint(0, len(y_names))
            selected_y_name = y_names[selected_y_idx]
            print(f'Epoch {epoch}: 随机选择 {selected_y_name}')
        
        for data in train_dataset:
            x1 = data['sequence_onehot']
            
            if train_mode == 'single':
                y = data['y']
                train_step(model_new, optimizer, x1, y)
                train_pred, out = test_step(model_new, x1, y)
                outs.append(out)
            elif train_mode == 'mixed':
                y_list = data['y_list']
                train_step_mixed(model_new, optimizer, x1, y_list)
                train_pred, out = test_step(model_new, x1, y_list[0])
                outs.append(out)
            elif train_mode == 'random':
                y = data['y_list'][selected_y_idx]
                train_step(model_new, optimizer, x1, y)
                train_pred, out = test_step(model_new, x1, y)
                outs.append(out)
                
        for data_test in test_dataset:
            x1_test = data_test['sequence_onehot']
            
            if train_mode == 'single':
                y_test = data_test['y']
                out_test_pred, out_test_loss = test_step(model_new, x1_test, y_test)
            elif train_mode in ['mixed', 'random']:
                y_test = data_test['y_list'][0]
                out_test_pred, out_test_loss = test_step(model_new, x1_test, y_test)
                
            y_predict.append(out_test_pred)
            y_lable.append(y_test)
        y_predict = list(itertools.chain.from_iterable(y_predict))
        y_lable = list(itertools.chain.from_iterable(y_lable))
        y_predict_plt=jnp.array(y_predict)
        y_lable_plt=jnp.array(y_lable)
        y0 = pd.DataFrame(jnp.squeeze(y_predict_plt))
        y1 = pd.DataFrame(jnp.squeeze(y_lable_plt))
        corr, p = spearmanr(y0,y1)
        print(f'Epoch: {epoch},  Loss: {sum(outs) / len(outs):4f}, test_r2_score: {corr}')
        print(f'file_name: {file_name}')
        if early_stopping(corr, model_new):
            print(f"早停触发！在第 {epoch} 个epoch停止训练")
            print(f"最佳相关系数: {early_stopping.best_corr:.4f}")
            model_new = early_stopping.restore_weights(model_new)
            break
            
        if corr > 0.98:
            print(f"相关系数达到阈值 0.98，在第 {epoch} 个epoch停止训练")
            break

    graph,state = nnx.split(model_new)
    if train_mode == 'single':
        save_name = f'../pretrain_weight/pretrain-{file_name}-{y_names[0]}.pkl'
    else:
        y_names_str = '-'.join(y_names)
        save_name = f'../pretrain_weight/pretrain-{file_name}-{train_mode}.pkl'
    with open(save_name, 'wb') as f:
        pickle.dump(state, f)
    print(f'模型已保存到: {save_name}')
