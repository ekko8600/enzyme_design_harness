import jax
import jax.numpy as jnp
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import itertools
from scipy.stats import spearmanr
from sklearn.metrics import r2_score
from sklearn.metrics import average_precision_score
from sklearn.metrics import recall_score, precision_score, f1_score, matthews_corrcoef,mean_squared_error

def plot_training_curves(training_stats):
    if not training_stats:
        print("没有训练统计数据可绘制")
        return
    steps = range(len(training_stats))
    losses = [stat['dpo_loss'] for stat in training_stats]
    accuracies = [stat['reward_accuracy'] for stat in training_stats]
    margins = [stat['reward_margin'] for stat in training_stats]
    
    fig, axes = plt.subplots(1, 3, figsize=(15, 5))
    
    axes[0].plot(steps, losses, alpha=0.7)
    axes[0].set_xlabel('Training Steps')
    axes[0].set_ylabel('DPO Loss')
    axes[0].set_title('Training Loss')
    axes[0].grid(True, alpha=0.3)
    
    axes[1].plot(steps, accuracies, alpha=0.7, color='green')
    axes[1].set_xlabel('Training Steps')
    axes[1].set_ylabel('Reward Accuracy')
    axes[1].set_title('Preference Accuracy')
    axes[1].grid(True, alpha=0.3)
    
    axes[2].plot(steps, margins, alpha=0.7, color='orange')
    axes[2].set_xlabel('Training Steps')
    axes[2].set_ylabel('Reward Margin')
    axes[2].set_title('Reward Margin')
    axes[2].grid(True, alpha=0.3)
    
    plt.tight_layout()
    # plt.savefig('dpo_training_curves.png', dpi=300)
    plt.show()
    
    print("训练曲线已保存为 dpo_training_curves.png")

def evaluate_with_recall(model, test_d, threshold_percentiles=[70, 80, 90], top_k_values=[100, 500]):
    y_true = []
    y_pred = []
    
    for data in test_d:
        x1 = data['sequence_onehot']
        y  = data['y']
        
        out_test = model(x1)['global']
        y_pred.append(out_test)
        y_true.append(y)
    
    y_pred = list(itertools.chain.from_iterable(y_pred))
    y_true = list(itertools.chain.from_iterable(y_true))
    
    y_pred = np.array(jax.device_get(jnp.array(y_pred)))
    y_true = np.array(jax.device_get(jnp.array(y_true)))
    results = {}
    
    # 基于百分位数阈值的评估
    for percentile in threshold_percentiles:
        threshold = np.percentile(y_true, percentile)
        
        y_true_binary = (y_true >= threshold).astype(int)
        y_pred_binary = (y_pred >= threshold).astype(int)
        
        recall = recall_score(y_true_binary, y_pred_binary)
        precision = precision_score(y_true_binary, y_pred_binary, zero_division=0)
        f1 = f1_score(y_true_binary, y_pred_binary, zero_division=0)
        mcc = matthews_corrcoef(y_true_binary, y_pred_binary)
        
        if len(np.unique(y_true_binary)) > 1:
            avg_precision = average_precision_score(y_true_binary, y_pred)
        else:
            avg_precision = 0.0
        
        results[f'percentile_{percentile}'] = {
            'threshold': float(threshold),
            'recall': float(recall),
            'precision': float(precision),
            'f1_score': float(f1),
            'mcc': float(mcc),
            'avg_precision': float(avg_precision),
            'n_positive': int(np.sum(y_true_binary)),
            'n_total': len(y_true_binary)
        }
        
        print(f"\n=== 阈值: {percentile}th percentile (score >= {threshold:.3f}) ===")
        print(f"正样本数量: {np.sum(y_true_binary)}/{len(y_true_binary)}")
        print(f"Recall: {recall:.4f}")
        print(f"Precision: {precision:.4f}")
        print(f"F1-Score: {f1:.4f}")
        print(f"MCC: {mcc:.4f}")
        print(f"Average Precision: {avg_precision:.4f}")
    
    return results

def evaluate_with_spearman(model,test_d):
    y_lable=[]
    y_predict=[]
    for data in test_d:
        x1 = data['sequence_onehot']
        y  = data['y']
        out_test = model(x1)['global']
        y_predict.append(out_test)
        y_lable.append(y)
    y_predict = list(itertools.chain.from_iterable(y_predict))
    y_lable = list(itertools.chain.from_iterable(y_lable))
    y_predict_plt=jnp.array(y_predict)
    y_lable_plt=jnp.array(y_lable)
    y0 = pd.DataFrame(jnp.squeeze(y_predict_plt))
    y1 = pd.DataFrame(jnp.squeeze(y_lable_plt))
    corr, p = spearmanr(y0,y1)
    mse = mean_squared_error(y1, y0)  # 计算MSE
    #plot
    print('r2_score: %.2f'% r2_score(y0, y1))
    print('MSE: %.4f'% mse)  # 输出MSE结果
    print("Spearman",corr)
    plt.figure(figsize=(10, 8))
    from scipy.stats import gaussian_kde
    density = gaussian_kde(np.vstack([y0.values.flatten(), y1.values.flatten()]))(np.vstack([y0.values.flatten(), y1.values.flatten()]))
    scatter = plt.scatter(y0, y1, c=density, cmap='viridis', alpha=0.7, s=50, edgecolors='white', linewidth=0.5)
    plt.colorbar(scatter, label='data density')
    min_val, max_val = min(y0.min().min(), y1.min().min()), max(y0.max().max(), y1.max().max())
    plt.plot([min_val, max_val], [min_val, max_val], 'r--', linewidth=2, alpha=0.8, label='ideal prediction line')
    plt.xlabel('Predicted', fontsize=14, fontweight='bold')
    plt.ylabel('True', fontsize=14, fontweight='bold')
    plt.legend()
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.show()
    return corr, mse
