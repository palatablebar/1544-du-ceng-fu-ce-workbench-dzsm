"""试镀签认：用冻结厚差树与成熟校准实绩比较联动推演的前后区间。

联动推演只给出点估计变化时，省工的改法可能只是点估计占优。签发下一挂试镀建议
必须以区间判断：调整后区间上界低于原挂区间下界才算改善，且比较必须使用未舍入的
边界值。缺成熟校准实绩时无法比较区间，转为待核；调整后超出训练包络或触发超流
暂停规则时直接淘汰。
"""

from dataclasses import dataclass

ADOPT = '建议'
PENDING = '待核'
ELIMINATED = '淘汰'
MAINTAIN = '维持'
NO_OPTION = '无可试镀'

APPROVED = '改善'
UNPROVEN = '未改善'

NO_CALIBRATION_CODE = 'INSUFFICIENT_CALIBRATION'
OUT_OF_TRAINING_CODE = 'OUT_OF_TRAINING'
DENSITY_LIMIT_CODE = 'DENSITY_LIMIT'

ELIMINATION_TEXT = {
    OUT_OF_TRAINING_CODE: '调整后超出训练范围',
    DENSITY_LIMIT_CODE: '调整后触发超流暂停',
}


@dataclass(frozen=True, slots=True)
class Scenario:
    """一次联动推演在调整后的点估计与校准区间。"""

    case: str
    work: int
    estimate: float
    lower: float | None
    upper: float | None
    out_of_training: bool
    hard_pause: bool

    @property
    def elimination(self):
        # 硬约束优先于区间比较：先淘汰不可执行的调整。
        if self.out_of_training:
            return OUT_OF_TRAINING_CODE
        if self.hard_pause:
            return DENSITY_LIMIT_CODE
        return None


@dataclass(frozen=True, slots=True)
class Outcome:
    """原挂具的下一挂试镀建议。"""

    status: str
    case: str | None
    work: int
    reason: str


def improves(scenario, base_lower):
    """调整后区间上界严格低于原挂区间下界。

    边界保持未舍入的浮点值，避免四舍五入把一次重叠或擦边的调整误签为改善。
    """
    return base_lower is not None and scenario.upper is not None and scenario.upper < base_lower


def label(scenario, base_lower, calibrated):
    """返回单个候选的签认标签与说明。"""
    code = scenario.elimination
    if code is not None:
        return ELIMINATED, ELIMINATION_TEXT[code]
    if not calibrated:
        return PENDING, '缺少成熟校准实绩，无法比较调整前后区间'
    if improves(scenario, base_lower):
        return APPROVED, '调整后上界低于原下界'
    return UNPROVEN, '调整后上界未低于原下界'


def candidate_evidence(scenario, base_lower, calibrated):
    """候选的区间证据，供工作台按单回查签认理由。"""
    state, note = label(scenario, base_lower, calibrated)
    return {'调整后估计':None if scenario.estimate is None else round(scenario.estimate,4),
            '调整后区间':[None if scenario.lower is None else round(scenario.lower,4),
                          None if scenario.upper is None else round(scenario.upper,4)],
            '训练域状态':'超出' if scenario.out_of_training else '涵盖',
            '签认':state,'签认说明':note}


def resolve(scenarios, *, base_lower, calibrated):
    """在全部候选中按“先保证改善、再最省工”签发一条建议。"""
    if not scenarios:
        return Outcome(NO_OPTION, None, 0, '没有可执行的联动试镀情形')
    valid = tuple(scenario for scenario in scenarios if scenario.elimination is None)
    if not calibrated:
        if not valid:
            return Outcome(ELIMINATED, None, 0, '候选均超出训练范围或触发超流暂停，且缺少成熟校准实绩')
        return Outcome(PENDING, None, 0, '缺少成熟校准实绩，无法比较调整前后区间')
    approved = tuple(scenario for scenario in valid if improves(scenario, base_lower))
    if approved:
        # 已确认区间改善后，取用量最省的方案；同用量按情形名定序，保证换序重算一致。
        chosen = min(approved, key=lambda scenario: (scenario.work, scenario.case))
        reason = (f"调整后区间上界 {chosen.upper:.10g} 低于原下界 {base_lower:.10g}"
                  f"（改善幅度 {base_lower - chosen.upper:.10g}），且为改善方案中最省工")
        return Outcome(ADOPT, chosen.case, chosen.work, reason)
    if valid:
        return Outcome(MAINTAIN, None, 0, '调整后区间上界未低于原下界，点估计变化不构成改善')
    return Outcome(ELIMINATED, None, 0, '候选均超出训练范围或触发超流暂停')
