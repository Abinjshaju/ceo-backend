"""Deterministic scenario calculations with explicit formulas, units, and assumptions.
Model reasoning never produces these numbers."""

from collections.abc import Callable

from ceo_backend.errors import Invalid

CALCULATION_VERSION = "1"


def _require(inputs: dict, *names: str) -> list[float]:
    values = []
    for name in names:
        value = inputs.get(name)
        if value is None:
            raise Invalid(f"{name} is required for this calculation.")
        try:
            values.append(float(value))
        except (TypeError, ValueError) as exc:
            raise Invalid(f"{name} must be a number.") from exc
    return values


def _positive(**values: float) -> None:
    for name, value in values.items():
        if value <= 0:
            raise Invalid(f"{name} must be greater than zero.")


def _round(value: float) -> float:
    return round(value, 2)


def roi(inputs: dict) -> dict:
    investment, annual_benefit, years = _require(inputs, "investment", "annual_benefit", "years")
    _positive(investment=investment, years=years)
    total = annual_benefit * years
    net = total - investment
    return {
        "outputs": {"total_benefit": _round(total), "net_return": _round(net), "roi_percent": _round(net / investment * 100)},
        "formula": "roi_percent = (annual_benefit × years − investment) ÷ investment × 100",
        "assumptions": ["Benefit is constant each year", "No discounting of future benefit"],
    }


def payback_period(inputs: dict) -> dict:
    investment, monthly_benefit = _require(inputs, "investment", "monthly_benefit")
    _positive(investment=investment, monthly_benefit=monthly_benefit)
    months = investment / monthly_benefit
    return {
        "outputs": {"payback_months": _round(months), "payback_years": _round(months / 12)},
        "formula": "payback_months = investment ÷ monthly_benefit",
        "assumptions": ["Benefit starts immediately and stays constant"],
    }


def npv(inputs: dict) -> dict:
    investment, rate = _require(inputs, "investment", "discount_rate_percent")
    flows = inputs.get("cash_flows") or []
    if not flows:
        raise Invalid("cash_flows is required: one value per year.")
    try:
        flows = [float(f) for f in flows]
    except (TypeError, ValueError) as exc:
        raise Invalid("cash_flows must be numbers.") from exc
    _positive(investment=investment)
    if rate <= -100:
        raise Invalid("discount_rate_percent must be above -100.")
    r = rate / 100
    present = sum(flow / (1 + r) ** year for year, flow in enumerate(flows, start=1))
    return {
        "outputs": {"present_value": _round(present), "npv": _round(present - investment)},
        "formula": "npv = Σ cash_flow_t ÷ (1 + rate)^t − investment, t = 1…n",
        "assumptions": ["Cash flows arrive at the end of each year", "Investment is paid up front"],
    }


def budget_change(inputs: dict) -> dict:
    investment, new_investment, annual_benefit, years = _require(
        inputs, "investment", "new_investment", "annual_benefit", "years"
    )
    _positive(investment=investment, new_investment=new_investment, years=years)
    scales = bool(inputs.get("benefit_scales_with_budget"))
    new_benefit = annual_benefit * new_investment / investment if scales else annual_benefit

    def roi_pct(cost: float, benefit: float) -> float:
        return (benefit * years - cost) / cost * 100

    return {
        "outputs": {
            "budget_change": _round(new_investment - investment),
            "budget_change_percent": _round((new_investment - investment) / investment * 100),
            "annual_benefit_after": _round(new_benefit),
            "roi_percent_before": _round(roi_pct(investment, annual_benefit)),
            "roi_percent_after": _round(roi_pct(new_investment, new_benefit)),
        },
        "formula": "roi_percent = (annual_benefit × years − investment) ÷ investment × 100, before and after",
        "assumptions": [
            "Benefit scales in proportion to budget" if scales else "Benefit is unchanged by the budget change",
            "Delivery date and scope are not modelled",
        ],
    }


CALCULATIONS: dict[str, Callable[[dict], dict]] = {
    "roi": roi,
    "payback_period": payback_period,
    "npv": npv,
    "budget_change": budget_change,
}


def calculate(calculation_type: str, inputs: dict, currency: str | None = None) -> dict:
    fn = CALCULATIONS.get(calculation_type)
    if fn is None:
        raise Invalid(f"Unknown calculation {calculation_type!r}. Use one of {', '.join(CALCULATIONS)}.")
    clean = {k: v for k, v in inputs.items() if v is not None}
    result = fn(clean)
    return {
        "calculation_type": calculation_type,
        "calculation_version": CALCULATION_VERSION,
        "inputs": clean,
        "currency": currency,
        **result,
    }
