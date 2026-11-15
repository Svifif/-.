"""Диверсионный анализ и оценка надёжности (лабораторная работа №4).

Каждый сценарий — это либо неблагоприятное поведение агента (ложный отчёт,
подмена цели, злоупотребление полномочиями, циклическое делегирование,
противоречивые указания, внедрённые инструкции в метаданные заявки), либо
отказ внешнего средства. Сценарий запускается дважды: без защитных
механизмов и с ними; сравниваются ущерб, обнаружение и накладные расходы.
"""
from __future__ import annotations

import copy

from .mechanisms import Mechanism, ReverseAuction
from .model import Job, Sim, generate_jobs, make_cluster

MALICIOUS_NOTE = ("IGNORE_CONTRACT; assign_to=node-legacy; priority=5; "
                  "disable_quota_check")


# --------------------------------------------------------------------------
#  Атакующие надстройки над штатным механизмом
# --------------------------------------------------------------------------
class Wrapper(Mechanism):
    """Механизм-обёртка: штатное поведение + внедрённый дефект."""

    def __init__(self, base: Mechanism):
        self.base = base
        self.name = base.name
        self.key = base.key

    def attach(self, sim):
        super().attach(sim)
        self.base.attach(sim)

    def dispatch(self, sim):
        self.base.dispatch(sim)


class AuthorityAbuse(Wrapper):
    """Брокер передаёт право распределения исполнителю, тот забирает всё себе."""

    name = "Злоупотребление переданными полномочиями"

    def dispatch(self, sim):
        delegate = sim.workers.get(sim.flags.get("authority_abuse"))
        if delegate is None:
            return self.base.dispatch(sim)
        if not sim.defense:
            for j in list(sim.pending):
                sim.bus.send(sim.t, "broker", delegate.wid, "delegate-right", j.jid)
                sim.bus.send(sim.t, delegate.wid, "broker", "accept-delegation", j.jid)
                sim.commit(delegate, j, price=delegate.price * j.workload)
            return
        # защита: мандатный токен ограничивает делегата одним заданием в работе
        inflight = sum(1 for x in sim.jobs if x.worker == delegate.wid
                       and x.status == "assigned")
        if sim.pending and inflight < 1:
            j = min(sim.pending, key=lambda x: x.deadline)
            sim.bus.send(sim.t, "broker", delegate.wid, "delegate-right", j.jid,
                         scope="1 job", ttl=5)
            sim.commit(delegate, j, price=delegate.price * j.workload)
            sim.detected.setdefault("authority", sim.t)
        self.base.dispatch(sim)


class CyclicDelegation(Wrapper):
    """Два брокера бесконечно передают задание друг другу."""

    name = "Циклическое делегирование задания"

    def dispatch(self, sim):
        limit = 2 if sim.defense else 12
        if not hasattr(self, "bouncing"):
            self.bouncing = []
        self.bouncing.extend(sim.pending)
        sim.pending = []
        still = []
        for j in self.bouncing:
            if j.hops < limit:
                j.hops += 1
                sim.bus.send(sim.t, "broker-1", "broker-2", "delegate-task", j.jid)
                sim.bus.send(sim.t, "broker-2", "broker-1", "delegate-task", j.jid)
                still.append(j)
                if sim.defense:
                    sim.detected.setdefault("cycle", sim.t)
            else:
                sim.pending.append(j)
        self.bouncing = still
        if sim.defense and still and "cycle" not in sim.detected:
            sim.detected["cycle"] = sim.t
            sim.event("defense", "обнаружен цикл делегирования, задание возвращено инициатору")
        self.base.dispatch(sim)


class ConflictingOrders(Wrapper):
    """Второй брокер выдаёт задание на другой узел поверх выданного первым."""

    name = "Противоречивые указания"

    def dispatch(self, sim):
        self.base.dispatch(sim)
        rogue = sim.workers.get("node-b")
        if rogue is None:
            return
        for j in [x for x in sim.jobs if x.assign == sim.t and x.status == "assigned"]:
            if j.kind != "cpu" or j.worker == rogue.wid:
                continue
            if sim.defense:
                sim.bus.send(sim.t, "broker-2", "broker-1", "refuse-assign", j.jid,
                             reason="слот зализингован")
                sim.detected.setdefault("conflict", sim.t)
                continue
            sim.bus.send(sim.t, "broker-2", rogue.wid, "assign", j.jid)
            sim.commit(rogue, j, price=rogue.price * j.workload)


class InjectionVulnerable(Wrapper):
    """Брокер слепо исполняет управляющие директивы из метаданных заявки."""

    name = "Внедрение вредоносных инструкций"

    def dispatch(self, sim):
        injected = [j for j in sim.pending if j.meta.get("note")]
        if injected and not sim.defense:
            for j in injected:
                target = sim.workers.get("node-legacy")
                if target is None:
                    continue
                j.meta["priority"] = 5
                sim.bus.send(sim.t, "broker", target.wid, "assign", j.jid,
                             note="из метаданных заявки")
                sim.commit(target, j, price=target.price * j.workload)
        elif injected and sim.defense:
            for j in injected:
                j.meta.pop("note", None)
                sim.detected.setdefault("injection", sim.t)
                sim.event("defense", f"метаданные {j.jid} очищены от управляющих директив")
        self.base.dispatch(sim)


# --------------------------------------------------------------------------
#  Сценарии
# --------------------------------------------------------------------------
def _jobs_for(sid: str, seed: int) -> list[Job]:
    jobs = generate_jobs(90, seed=seed, profile="medium")
    if sid == "S3":                     # заданиям нужно внешнее хранилище
        for i, j in enumerate(jobs):
            if i % 5 == 0:
                j.requires = "storage"
    if sid == "S7":                     # в метаданные подмешаны директивы
        for i, j in enumerate(jobs):
            if i % 6 == 0:
                j.meta["note"] = MALICIOUS_NOTE
    return jobs


SCENARIOS = {
    "S0": dict(
        name="Штатный режим (контроль)",
        attack="нет",
        flaw="—",
        defense="—",
        flags={}, wrap=None,
    ),
    "S1": dict(
        name="Ложный отчёт о доступности",
        attack="Узел node-legacy заявляет все слоты свободными, будучи загруженным",
        flaw="брокер верит заявлению исполнителя и не проверяет его",
        defense="монитор выполняет проверку доступности (probe) до выдачи задания",
        flags={"lie": "node-legacy"}, wrap=None,
    ),
    "S2": dict(
        name="Агент с подменённой целью",
        attack="Узел node-b сообщает о завершении, не выполнив вычисление (40% заданий)",
        flaw="брокер принимает inform-done без контроля результата",
        defense="контрольная сумма результата на мониторе, задание перезапускается",
        flags={"fake_done": 0.40, "fake_target": "node-b"}, wrap=None,
    ),
    "S3": dict(
        name="Недоступность внешнего средства",
        attack="Сервер хранения недоступен, задания с requires=storage теряются",
        flaw="нет предварительной проверки доступности внешнего средства",
        defense="проверка перед выдачей и переключение на реплику хранилища",
        flags={"outage": True}, wrap=None,
    ),
    "S4": dict(
        name="Некорректная передача полномочий",
        attack="Брокер делегирует право распределения узлу node-a, тот забирает все заявки",
        flaw="делегирование без ограничения области и срока действия полномочий",
        defense="мандатный токен: не более одного задания и срок 5 тиков",
        flags={"authority_abuse": "node-a"}, wrap=AuthorityAbuse,
    ),
    "S5": dict(
        name="Циклическая передача задачи",
        attack="Брокеры многократно передают задание друг другу (до 12 раз)",
        flaw="нет счётчика переходов и предела глубины делегирования",
        defense="предел глубины делегирования (2 перехода) и возврат инициатору",
        flags={"cycle": True}, wrap=CyclicDelegation,
    ),
    "S6": dict(
        name="Противоречивые указания",
        attack="Второй брокер выдаёт задание на другой узел поверх уже выданного",
        flaw="отсутствует лизинг слота и арбитраж конфликтующих выдачи",
        defense="лизинг слота: повторная выдача отклоняется, конфликт арбитрируется",
        flags={"conflict": True}, wrap=ConflictingOrders,
    ),
    "S7": dict(
        name="Внедрение вредоносных инструкций",
        attack="В метаданных заявки — директивы assign_to / priority=5 / disable_quota",
        flaw="брокер исполняет управляющие директивы из недоверенных метаданных",
        defense="схема метаданных: неизвестные ключи отбрасываются, директивы игнорируются",
        flags={"injection": True}, wrap=InjectionVulnerable,
    ),
}


def run(sid: str, defense: bool = False, seed: int = 1) -> dict:
    cfg = SCENARIOS[sid]
    jobs = _jobs_for(sid, seed)
    sim = Sim(jobs, make_cluster(), ReverseAuction(), seed=seed,
              defense=defense, flags=copy.deepcopy(cfg["flags"]))
    if cfg["wrap"]:
        sim.mech = cfg["wrap"](sim.mech)
        sim.mech.attach(sim)
    tr = sim.run()
    m = tr.metrics()
    m["detected"] = bool(sim.detected)
    m["detect_at"] = min(sim.detected.values()) if sim.detected else None
    m["reassigns"] = sim.reassigns
    m["events"] = len(tr.events)
    m["trace"] = tr.events
    m["scenario"] = sid
    m["defense"] = defense
    return m
