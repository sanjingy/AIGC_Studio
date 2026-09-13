"""伴随进程的测试。**只用标准库 unittest**，跑法：

```
python -m unittest discover -s apps/local_runner/tests -t .
```

不进 pytest 的 `testpaths`（那是后端的，要 Docker）。这一套的全部意义
就是在**没有后端依赖的桌面上**也能验证安全约束。
"""
