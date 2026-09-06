"""Approved synthetic date concretization, never a historical-day assertion."""
from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo
from deskpet.quality.corpus_c01 import payload

ZONE=ZoneInfo('Asia/Shanghai')
COARSE={
 'C03-02': {'E': ('season:spring',4)},
 'C03-17': {'E1': ('month:06',6), 'E2': ('month:08',8)},
}


def date_semantics(batch,spec):
    if spec[1]!='episode':return None
    month_group=COARSE.get(batch.case_id)
    if month_group is None:
        return dict(authored_precision='undated',synthetic_day=True,
            occurred_start=batch.scenario_time-86400,timezone='Asia/Shanghai',rule='scenario-clock-minus-24h')
    precision,month=month_group[spec[0]]
    clock=datetime.fromtimestamp(batch.scenario_time,ZONE)
    # One year for the whole authored E1 -> E2 group; never choose each month
    # independently (July clock must not reverse June -> August chronology).
    year=clock.year
    latest_month=max(value[1] for value in month_group.values())
    if datetime(year,latest_month,15,12,tzinfo=ZONE)>=clock:
        year-=1
    chosen=datetime(year,month,15,12,tzinfo=ZONE)
    return dict(authored_precision=precision,synthetic_day=True,occurred_start=chosen.timestamp(),
        timezone='Asia/Shanghai',rule='group-year/month-day15-local-noon')


def c03_payload(batch,spec):
    value=payload(spec,batch.scenario_time)
    semantics=date_semantics(batch,spec)
    if semantics is None:return value
    marker=('【原日期精度='+semantics['authored_precision']+
            '；synthetic_day=true；日时仅为合成fixture取值，非真实发生日，不可作为日期答案】')
    return replace(value,title=value.title+marker,occurred_start=semantics['occurred_start'])
