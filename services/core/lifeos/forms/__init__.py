"""表单系统（第 6 章）：YAML 定义驱动，前后端共用同一份定义。"""

from .loader import FieldDef, FormDef, FormRegistry, SectionDef

__all__ = ["FieldDef", "FormDef", "FormRegistry", "SectionDef"]
