"""用户上传的 Skill（ADR-026）。本轮只有数据层。

不导出 router：包的 __init__ 一旦 import router，就会把 auth.deps 等
一并拉进初始化链，撞循环导入。

与顶层 `skills/` 包的分工：`skills/` 是**文件系统层**的声明与校验
（registry/spec，内置与第三方 YAML）；这里是**数据库层**——
用户上传上来的那一份 YAML 及其校验结论存在哪。
"""

__all__: list[str] = []
