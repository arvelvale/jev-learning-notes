# Jev 学习笔记

课程「入门与核心 · 第 1 章 认识 Jev」的笔记与练习。

- `认识jev.md`：章节笔记
- `notebooks/01_认识Jev.py`：Notebook 导出的可运行脚本，含练习（工单升级人工：拆成 Score + 两个 Noul，由代码组合规则）

## 运行

```bash
pip install typesafe-sdk==0.7.0
# 密钥只通过环境变量传入，不要写进代码
export TYPESAFE_API_KEY=你的key   # PowerShell: $env:TYPESAFE_API_KEY="..."
export JEV_RUN_MODE=live          # 不设置密钥时默认 auto，回退离线替身
python notebooks/01_认识Jev.py
```
