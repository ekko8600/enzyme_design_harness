1.预训练数据准备：对目标蛋白单点饱和突变，以及2-5点随机采样，构建4-10万条突变数据库(库容大小自行确定)。随后自行对这些突变序列进行fitness预测，
  常见模型包括(saprot，ESMIF，GEMME)，将预测结果作为预训练数据。

2.执行./model_run/pretrain.py进行预训练，预训练模式包括：
  单一目标训练(single)：仅使用一个y_name进行训练
  混合蒸馏训练(mixed)：使用所有y_name进行训练
  随机蒸馏训练(random)：每个epoch随机选择一个y_name进行训练

3.执行./model_run/finetune_SFT.py进行微调;

4.执行./model_run/evaluate.py加载微调好的模型进行大规模序列评估；
