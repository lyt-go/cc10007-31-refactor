"""接口出入参模型：列表分页、动作结果与各模块的明细结构。"""
from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PageResult(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int = 1
    size: int = 20


class ActionResult(BaseModel):
    ok: bool
    message: str
    entry: dict[str, Any] | None = None


class EntryPayload(BaseModel):
    """登记或修改一条业务记录时提交的字段集合。"""

    values: dict[str, Any] = Field(default_factory=dict)
    remark: str | None = None



class SequenceEntry(BaseModel):
    """序列模式明细结构。"""

    field_0: str | None = None  # 模式编号
    field_1: str | None = None  # 用户分群
    field_2: str | None = None  # 事件序列
    field_3: str | None = None  # 最大步长
    field_4: str | None = None  # 最小支持度
    field_5: str | None = None  # 时间窗口天
    field_6: str | None = None  # 序列频次
    field_7: str | None = None  # 转化率

class RfmEntry(BaseModel):
    """分层规则明细结构。"""

    field_0: str | None = None  # 规则编号
    field_1: str | None = None  # 最近消费天界
    field_2: str | None = None  # 消费频次界
    field_3: str | None = None  # 消费金额界
    field_4: str | None = None  # 分层层级
    field_5: str | None = None  # 层间流转规则
    field_6: str | None = None  # 更新周期
    field_7: str | None = None  # 覆盖用户量

class ChurnEntry(BaseModel):
    """预警模型明细结构。"""

    field_0: str | None = None  # 模型编号
    field_1: str | None = None  # 模型名称
    field_2: str | None = None  # 特征变量
    field_3: str | None = None  # 流失定义天
    field_4: str | None = None  # 训练数据集
    field_5: str | None = None  # 预测阈值
    field_6: str | None = None  # 模型准确率
    field_7: str | None = None  # 预警推送频率

class RecommendEntry(BaseModel):
    """推荐策略明细结构。"""

    field_0: str | None = None  # 策略编号
    field_1: str | None = None  # 推荐场景
    field_2: str | None = None  # 召回算法
    field_3: str | None = None  # 排序模型
    field_4: str | None = None  # 冷启动策略
    field_5: str | None = None  # 多样性权重
    field_6: str | None = None  # 刷新频率
    field_7: str | None = None  # 推荐条数上限

class AttrmodelEntry(BaseModel):
    """归因模型明细结构。"""

    field_0: str | None = None  # 模型编号
    field_1: str | None = None  # 模型名称
    field_2: str | None = None  # 归因逻辑
    field_3: str | None = None  # 触点权重分布
    field_4: str | None = None  # 衰减天数
    field_5: str | None = None  # 转化窗口
    field_6: str | None = None  # 直接贡献率
    field_7: str | None = None  # 间接贡献率

class ActivityEntry(BaseModel):
    """活动方案明细结构。"""

    field_0: str | None = None  # 方案编号
    field_1: str | None = None  # 活动名称
    field_2: str | None = None  # 活动类型
    field_3: str | None = None  # 目标人群
    field_4: str | None = None  # 核心指标
    field_5: str | None = None  # 对照方案
    field_6: str | None = None  # 评估周期天
    field_7: str | None = None  # 显著性阈值

class RealtimeEntry(BaseModel):
    """决策规则明细结构。"""

    field_0: str | None = None  # 规则编号
    field_1: str | None = None  # 触发事件
    field_2: str | None = None  # 决策条件
    field_3: str | None = None  # 执行动作
    field_4: str | None = None  # 冷却时间秒
    field_5: str | None = None  # 优先级
    field_6: str | None = None  # 分流比例
    field_7: str | None = None  # 生效时段

class BudgetEntry(BaseModel):
    """预算方案明细结构。"""

    field_0: str | None = None  # 方案编号
    field_1: str | None = None  # 预算总额
    field_2: str | None = None  # 渠道分配比例
    field_3: str | None = None  # 人群分配比例
    field_4: str | None = None  # 时段分配策略
    field_5: str | None = None  # 消耗速率上限
    field_6: str | None = None  # 调整周期
    field_7: str | None = None  # ROI目标

class LifetimeEntry(BaseModel):
    """LTV模型明细结构。"""

    field_0: str | None = None  # 模型编号
    field_1: str | None = None  # 模型名称
    field_2: str | None = None  # 估算方法
    field_3: str | None = None  # 观察窗口天
    field_4: str | None = None  # 预测窗口天
    field_5: str | None = None  # 折现率
    field_6: str | None = None  # 客单价因子
    field_7: str | None = None  # 留存率因子

class AbvariantEntry(BaseModel):
    """变体方案明细结构。"""

    field_0: str | None = None  # 变体编号
    field_1: str | None = None  # 关联实验
    field_2: str | None = None  # 变体名称
    field_3: str | None = None  # 变体描述
    field_4: str | None = None  # 分流占比
    field_5: str | None = None  # 配置参数集
    field_6: str | None = None  # 指标期望
    field_7: str | None = None  # 生效时间范围

class DashboardEntry(BaseModel):
    """看板视图明细结构。"""

    field_0: str | None = None  # 视图编号
    field_1: str | None = None  # 视图名称
    field_2: str | None = None  # 指标组合
    field_3: str | None = None  # 图表布局
    field_4: str | None = None  # 数据刷新秒
    field_5: str | None = None  # 对比周期
    field_6: str | None = None  # 告警线配置
    field_7: str | None = None  # 订阅接收组

class ScorecardEntry(BaseModel):
    """评分卡明细结构。"""

    field_0: str | None = None  # 评分编号
    field_1: str | None = None  # 评分对象
    field_2: str | None = None  # 特征变量组
    field_3: str | None = None  # 变量分箱
    field_4: str | None = None  # WOE编码
    field_5: str | None = None  # 变量权重
    field_6: str | None = None  # 基准分数
    field_7: str | None = None  # 评分刻度

class ClusterEntry(BaseModel):
    """聚类任务明细结构。"""

    field_0: str | None = None  # 任务编号
    field_1: str | None = None  # 聚类算法
    field_2: str | None = None  # 特征维度
    field_3: str | None = None  # 目标聚类数
    field_4: str | None = None  # 距离度量
    field_5: str | None = None  # 迭代次数上限
    field_6: str | None = None  # 聚类标签
    field_7: str | None = None  # 轮廓系数

class ComparisonEntry(BaseModel):
    """对比组明细结构。"""

    field_0: str | None = None  # 对比编号
    field_1: str | None = None  # 对比名称
    field_2: str | None = None  # 实验组筛选
    field_3: str | None = None  # 对照组筛选
    field_4: str | None = None  # 对比指标
    field_5: str | None = None  # 时间对齐
    field_6: str | None = None  # 显著性检验
    field_7: str | None = None  # 效应量

class DatadriftEntry(BaseModel):
    """漂移检测明细结构。"""

    field_0: str | None = None  # 检测编号
    field_1: str | None = None  # 监控数据集
    field_2: str | None = None  # 基线版本
    field_3: str | None = None  # 漂移度量
    field_4: str | None = None  # 漂移阈值
    field_5: str | None = None  # 检测频率
    field_6: str | None = None  # 告警接收人
    field_7: str | None = None  # 最近漂移值

class PushplanEntry(BaseModel):
    """推送方案明细结构。"""

    field_0: str | None = None  # 方案编号
    field_1: str | None = None  # 推送内容
    field_2: str | None = None  # 目标人群
    field_3: str | None = None  # 推送渠道
    field_4: str | None = None  # 推送时间
    field_5: str | None = None  # 频控策略
    field_6: str | None = None  # AB分组标识
    field_7: str | None = None  # 预计触达量

class MetricdrillEntry(BaseModel):
    """归因分析明细结构。"""

    field_0: str | None = None  # 分析编号
    field_1: str | None = None  # 目标指标
    field_2: str | None = None  # 波动方向
    field_3: str | None = None  # 波动幅度
    field_4: str | None = None  # 维度下钻路径
    field_5: str | None = None  # 贡献度排名
    field_6: str | None = None  # 根因假设
    field_7: str | None = None  # 验证结果

class TacticlibEntry(BaseModel):
    """运营策略明细结构。"""

    field_0: str | None = None  # 策略编号
    field_1: str | None = None  # 策略名称
    field_2: str | None = None  # 策略类型
    field_3: str | None = None  # 触发条件
    field_4: str | None = None  # 执行动作
    field_5: str | None = None  # 生效周期
    field_6: str | None = None  # 策略评分
    field_7: str | None = None  # 使用次数


class DecisionRegisterPayload(BaseModel):
    """登记执行明细和确认动作共用的入参。

    ``sessions`` 里每条生效会话按原口径给出触发对象、触发条件、优先级与执行动作；
    ``auto_confirm`` 默认不自动闭环，登记先落「待处理明细」，核对后再确认。
    """

    sessions: list[dict[str, Any]] = Field(default_factory=list)
    auto_confirm: bool = False
    action: str | None = None
