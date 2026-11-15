"""Ядро МАС DiSMA: агенты, сообщения, журнал трассы, дискретно-событийная модель.

Модель времени — целочисленные тики. У исполнителя есть несколько слотов;
каждый слот характеризуется моментом освобождения. Задание, поставленное
исполнителю, занимает один слот на ceil(workload / speed) тиков.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from .config import C_MSG, C_TICK, HORIZON


# --------------------------------------------------------------------------
#  Сущности
# --------------------------------------------------------------------------
@dataclass
class Job:
    """Задание (заявка заказчика)."""
    jid: str
    client: str
    workload: float
    deadline: int
    submit: int
    priority: int = 2          # 1..5, 5 — наивысший
    kind: str = "cpu"          # требуемый класс ресурса
    max_price: float = 60.0    # предельная цена, которую готов платить заказчик
    requires: str = ""         # внешнее средство, без которого задание не выполнимо
    meta: dict = field(default_factory=dict)   # метаданные (недоверенные данные)
    # --- динамика ---
    worker: str | None = None
    assign: int | None = None
    start: int | None = None
    finish: int | None = None
    price: float = 0.0
    status: str = "new"        # new|pending|assigned|running|done|failed|rejected
    attempts: int = 0
    hops: int = 0              # глубина делегирования (для контроля циклов)
    claim_priority: int | None = None   # заявленный заказчиком приоритет
    note: str = ""

    def duration(self, speed: float) -> int:
        return max(1, int(math.ceil(self.workload / speed)))

    @property
    def lateness(self) -> int:
        if self.finish is None:
            return 0
        return max(0, self.finish - self.deadline)

    @property
    def sla_ok(self) -> bool:
        return self.status == "done" and self.finish is not None and self.finish <= self.deadline


@dataclass
class Worker:
    """Агент-исполнитель (узел кластера)."""
    wid: str
    speed: float
    capacity: int
    price: float
    kinds: tuple = ("cpu",)
    trust: float = 1.0
    slots_free: list = field(default_factory=list)
    busy_ticks: int = 0
    done: int = 0

    def __post_init__(self):
        if not self.slots_free:
            self.slots_free = [0] * self.capacity

    # --- фактическое состояние (никогда не врёт) ---
    def free_slots(self, t: int) -> int:
        return sum(1 for f in self.slots_free if f <= t)

    def earliest_free(self) -> int:
        return min(self.slots_free)

    def eft(self, t: int, dur: int) -> int:
        """Фактический момент завершения при постановке в самый ранний слот."""
        return max(t, self.earliest_free()) + dur

    def load_factor(self, t: int) -> float:
        """Загрузка: суммарное остаточное время слотов к некоторому горизонту."""
        horizon = 24.0
        return max(0.0, min(1.0, (sum(max(0, f - t) for f in self.slots_free))
                            / (self.capacity * horizon)))


@dataclass
class Message:
    t: int
    src: str
    dst: str
    perf: str
    jid: str | None = None
    body: dict = field(default_factory=dict)


# --------------------------------------------------------------------------
#  Канал связи: все сообщения агентов проходят через шину
# --------------------------------------------------------------------------
class Bus:
    def __init__(self, limit: int = 600):
        self.count = 0
        self.by_perf: dict[str, int] = {}
        self.sample: list[Message] = []
        self.limit = limit

    def send(self, t: int, src: str, dst: str, perf: str, jid: str | None = None, **body) -> None:
        self.count += 1
        self.by_perf[perf] = self.by_perf.get(perf, 0) + 1
        if len(self.sample) < self.limit:
            self.sample.append(Message(t, src, dst, perf, jid, body))


# --------------------------------------------------------------------------
#  Симулятор
# --------------------------------------------------------------------------
class Sim:
    """Дискретно-событийный симулятор многоагентной системы."""

    def __init__(self, jobs, workers, mechanism, seed: int = 0,
                 horizon: int = HORIZON, defense: bool = False,
                 flags: dict | None = None, quota: dict | None = None,
                 trace_limit: int = 4000):
        self.jobs: list[Job] = jobs
        self.workers: dict[str, Worker] = {w.wid: w for w in workers}
        self.pending: list[Job] = []
        self.t = 0
        self.bus = Bus()
        self.events: list[tuple] = []
        self.trace_limit = trace_limit
        self.defense = defense
        self.flags = dict(flags or {})
        self.quota = dict(quota or {})
        self.seed = seed
        self.rng = random.Random(seed)
        self.dispatch_ticks = 0
        self.mech = mechanism
        self.mech.attach(self)
        self.detected: dict[str, float] = {}
        self.damage: dict[str, float] = {}
        self.horizon = horizon
        self.client_usage: dict[str, int] = {}
        self.reassigns = 0

    # ---------------- служебное ----------------
    def event(self, kind: str, text: str) -> None:
        if len(self.events) < self.trace_limit:
            self.events.append((self.t, kind, text))

    def flag(self, name: str):
        return self.flags.get(name)

    def target(self, name: str) -> str | None:
        v = self.flags.get(name)
        return v if isinstance(v, str) else None

    # ---------------- доступность: заявленная и проверенная ----------------
    def availability(self, w: Worker) -> int:
        """Сколько слотов исполнитель заявляет как свободные.

        При атаке «ложный отчёт» агент заявляет полную доступность.
        В режиме защиты брокер не верит заявлению и запрашивает проверку
        у монитора (2 сообщения на проверку).
        """
        lie = self.target("lie")
        if lie == w.wid:
            if self.defense and self.t >= self.flags.get("defense_from", 0):
                self.bus.send(self.t, "monitor", w.wid, "request-probe")
                self.bus.send(self.t, w.wid, "monitor", "inform-probe")
                self.detected.setdefault("lie", self.t)
                return w.free_slots(self.t)
            return w.capacity
        return w.free_slots(self.t)

    def claimed_eft(self, w: Worker, dur: int) -> int:
        """Оценка момента завершения по заявленным данным."""
        lie = self.target("lie")
        if lie == w.wid:
            if self.defense:
                self.bus.send(self.t, "monitor", w.wid, "request-probe")
                self.bus.send(self.t, w.wid, "monitor", "inform-probe")
                self.detected.setdefault("lie", self.t)
                return w.eft(self.t, dur)
            return self.t + dur            # «свободен, начну сразу»
        return w.eft(self.t, dur)

    # ---------------- постановка задания ----------------
    def commit(self, w: Worker, job: Job, price: float = 0.0) -> tuple[int, int]:
        """Фактическая постановка задания в самый ранний слот исполнителя."""
        dur = job.duration(w.speed)
        i = min(range(w.capacity), key=lambda k: w.slots_free[k])
        start = max(self.t, w.slots_free[i])
        finish = start + dur
        w.slots_free[i] = finish
        w.busy_ticks += dur
        job.worker = w.wid
        job.assign = self.t
        job.start = start
        job.finish = finish
        job.price = price
        job.status = "assigned"
        if job in self.pending:
            self.pending.remove(job)
        self.client_usage[job.client] = self.client_usage.get(job.client, 0) + 1
        return start, finish

    def keep_pending(self, job: Job) -> None:
        job.status = "pending"

    # ---------------- главный цикл ----------------
    def run(self) -> "Trace":
        by_submit: dict[int, list[Job]] = {}
        for j in self.jobs:
            by_submit.setdefault(j.submit, []).append(j)

        t = 0
        while t <= self.horizon:
            self.t = t
            for j in by_submit.pop(t, []):
                # недоступность внешнего средства
                if j.requires and self.flags.get("outage") and not j.meta.get("replica"):
                    if self.defense:
                        self.bus.send(t, "broker", "storage-replica", "request-use", j.jid)
                        self.detected.setdefault("outage", t)
                        j.meta["replica"] = True
                        by_submit.setdefault(t + 2, []).append(j)
                        continue
                    j.status = "failed"
                    self.damage["lost"] = self.damage.get("lost", 0) + 1
                    self.event("attack", f"внешнее средство {j.requires} недоступно, "
                                         f"{j.jid} потеряно")
                    continue
                j.status = "pending"
                self.pending.append(j)
                self.bus.send(t, j.client, "broker", "request", j.jid)
                self.event("submit", f"{j.client} -> брокер: request({j.jid})")

            before = len(self.pending)
            self.mech.dispatch(self)
            if len(self.pending) < before or before:
                self.dispatch_ticks += 1

            self._complete(t)
            self._advance(t)

            if not self.pending and not by_submit and not self._running():
                break
            t += 1

        self.t = t
        return Trace(self)

    # ---------------- завершение и продвижение ----------------
    def _complete(self, t: int) -> None:
        for w in self.workers.values():
            for j in [x for x in self.jobs if x.worker == w.wid and x.finish == t
                      and x.status == "assigned"]:
                fake = self.flags.get("fake_done") \
                    and self.flags.get("fake_target") in (None, w.wid) \
                    and self.rng.random() < self.flags["fake_done"]
                if fake:
                    j.status = "corrupt"
                    self.damage["corrupt"] = self.damage.get("corrupt", 0) + 1
                    self.bus.send(t, w.wid, "broker", "inform-done", j.jid, bad=True)
                    self.event("attack", f"{w.wid} сообщает о завершении {j.jid}, "
                                         f"результат недостоверен")
                    continue
                j.status = "done"
                w.done += 1
                self.bus.send(t, w.wid, "broker", "inform-done", j.jid)
                self.event("done", f"{w.wid} -> брокер: inform-done({j.jid})")

        # аудит монитора: повреждённые результаты возвращаются в работу
        if self.defense:
            for j in self.jobs:
                if j.status == "corrupt" and j.attempts < 2:
                    j.attempts += 1
                    j.status = "pending"
                    j.worker = None
                    j.finish = None
                    j.start = None
                    self.reassigns += 1
                    self.detected.setdefault("corrupt", self.t)
                    self.pending.append(j)
                    self.bus.send(self.t, "monitor", "broker", "reassign", j.jid)
                    self.event("defense", f"монитор: контрольная сумма {j.jid} не совпала, перезапуск")

    def _running(self) -> bool:
        return any(j.status == "assigned" for j in self.jobs)

    def _advance(self, t: int) -> None:
        for j in self.jobs:
            if j.status == "assigned" and j.start == t and t < t + (j.finish - j.start):
                j.status = "assigned"

    def metrics(self) -> dict:
        return Trace(self).metrics()


# --------------------------------------------------------------------------
#  Трасса выполнения и метрики
# --------------------------------------------------------------------------
class Trace:
    def __init__(self, sim: Sim):
        self.sim = sim
        self.jobs = sim.jobs
        self.messages = sim.bus.count
        self.by_perf = dict(sim.bus.by_perf)
        self.events = sim.events
        self.sample = sim.bus.sample

    # ---------- агрегированные показатели ----------
    def metrics(self) -> dict:
        jobs = self.jobs
        done = [j for j in jobs if j.status == "done"]
        failed = [j for j in jobs if j.status in ("failed", "corrupt")]
        finished = [j for j in jobs if j.finish is not None]
        n = len(jobs)
        makespan = max((j.finish for j in finished), default=0)
        tat = [j.finish - j.submit for j in finished]
        waits = [j.start - j.assign for j in finished if j.start is not None and j.assign is not None]
        sla_bad = [j for j in finished if j.finish > j.deadline] + failed
        busy = sum(w.busy_ticks for w in self.sim.workers.values())
        cap_total = sum(w.capacity for w in self.sim.workers.values()) * max(1, makespan)
        return {
            "jobs": n,
            "done": len(done),
            "failed": len(failed),
            "makespan": makespan,
            "mean_tat": _mean(tat),
            "p95_tat": _pct(tat, 95),
            "max_wait": max(waits) if waits else 0,
            "mean_wait": _mean(waits),
            "sla_bad": len(sla_bad),
            "sla_rate": len(sla_bad) / n if n else 0.0,
            "messages": self.messages,
            "msg_per_job": self.messages / n if n else 0.0,
            "coord_cost": self.messages * C_MSG + self.sim.dispatch_ticks * C_TICK,
            "exec_cost": sum(j.price for j in finished),
            "utilization": busy / cap_total if cap_total else 0.0,
            "gini": self.gini_workers(),
            "throughput": len(done) / makespan if makespan else 0.0,
            "reassigns": self.sim.reassigns,
        }

    def gini_workers(self) -> float:
        load = [w.busy_ticks for w in self.sim.workers.values()]
        return gini(load)

    def gini_clients(self) -> float:
        cl = sorted(set(j.client for j in self.jobs))
        vals = [sum(1 for j in self.jobs if j.client == c and j.status == "done") for c in cl]
        return gini(vals)

    def client_stats(self) -> dict:
        out = {}
        for c in sorted(set(j.client for j in self.jobs)):
            js = [j for j in self.jobs if j.client == c]
            fin = [j for j in js if j.finish is not None]
            out[c] = {
                "n": len(js),
                "done": sum(1 for j in js if j.status == "done"),
                "sla_bad": sum(1 for j in fin if j.finish > j.deadline),
                "mean_tat": _mean([j.finish - j.submit for j in fin]),
            }
        return out


# --------------------------------------------------------------------------
#  Вспомогательные статистические функции
# --------------------------------------------------------------------------
def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def _pct(xs, p):
    if not xs:
        return 0.0
    s = sorted(xs)
    k = max(0, min(len(s) - 1, int(round((p / 100) * (len(s) - 1)))))
    return s[k]


def gini(xs) -> float:
    """Коэффициент Джини (0 — идеальное равенство)."""
    xs = [x for x in xs if x is not None]
    n = len(xs)
    if n == 0:
        return 0.0
    s = sum(xs)
    if s == 0:
        return 0.0
    xs = sorted(xs)
    cum = sum((i + 1) * x for i, x in enumerate(xs))
    return (2 * cum) / (n * s) - (n + 1) / n


def jain(xs) -> float:
    """Индекс справедливости Джайна (1 — идеально)."""
    n = len(xs)
    if n == 0:
        return 0.0
    s = sum(xs) ** 2
    q = sum(x * x for x in xs)
    return s / (n * q) if q else 0.0


# --------------------------------------------------------------------------
#  Генерация нагрузки и кластера
# --------------------------------------------------------------------------
CLUSTER = [
    # id, скорость (MSU/тик), слотов, цена за MSU, поддерживаемые классы
    ("node-a", 3.0, 2, 1.00, ("cpu",)),
    ("node-b", 2.5, 2, 0.80, ("cpu",)),
    ("node-gpu", 4.0, 1, 2.50, ("cpu", "gpu")),
    ("node-legacy", 1.2, 3, 0.50, ("cpu",)),
]

PROFILES = {"light": 0.55, "medium": 0.80, "heavy": 1.05}


def make_cluster(spec=CLUSTER):
    return [Worker(wid, speed, cap, price, kinds) for wid, speed, cap, price, kinds in spec]


def generate_jobs(n: int = 120, seed: int = 1, profile: str = "medium",
                  clients: tuple = ("group-A", "group-B", "group-C"),
                  gpu_share: float = 0.25, slack: int = 12) -> list[Job]:
    """Поток заявок с заданной интенсивностью ρ = предлагаемая работа / мощность."""
    rng = random.Random(seed)
    rho = PROFILES.get(profile, 0.8)
    rate = sum(s * c for _, s, c, _, _ in CLUSTER)     # суммарная мощность кластера
    workload_total = n * 6.0
    span = max(10, int(workload_total / (rho * rate)))
    jobs = []
    for i in range(n):
        wl = max(1.0, min(20.0, rng.lognormvariate(1.60, 0.45)))
        kind = "gpu" if rng.random() < gpu_share else "cpu"
        client = clients[i % len(clients)]
        submit = int(round(i * span / max(1, n - 1))) + rng.randint(0, 2)
        prio = rng.choices([1, 2, 3, 4, 5], weights=[10, 25, 35, 20, 10])[0]
        dl = submit + int(2.2 * wl / 2.5) + slack
        jobs.append(Job(
            jid=f"J{i:03d}", client=client, workload=round(wl, 1), deadline=dl,
            submit=submit, priority=prio, kind=kind,
            max_price=round(3.0 * wl, 1),
        ))
    jobs.sort(key=lambda j: j.submit)
    return jobs
