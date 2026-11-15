"""Переговоры и разрешение конфликтов (лабораторная работа №3).

Ситуация: единственный GPU-узел с двумя слотами и очередью не более четырёх
заданий; три группы-заказчика с разными контрактными приоритетами и квотами.
Спрос устойчиво превышает предложение, поэтому каждое задание конкурирует
за слот.

Реализованы три режима распределения:
  fcfs      — «первый пришёл, первый обслужен», без переговоров;
  naive     — торг по цене без арбитража и проверки заявленных приоритетов;
  full      — торг + приоритезация по контракту + арбитраж + репутация и
              санкции за искажение заявок + соблюдение квот.
"""
from __future__ import annotations

import random

from .mechanisms import Mechanism
from .model import Job, Worker

CONTRACT_PRIORITY = {"group-A": 4, "group-B": 3, "group-C": 2}
CONTRACT_QUOTA = {"group-A": 0.55, "group-B": 0.30, "group-C": 0.15}

# профили групп: интерактивные, бизнес- и пакетные задания
PROFILE = {
    "group-A": dict(prio=4, slack=6.0, wl=(2.0, 5.0), claim=0.0),
    "group-B": dict(prio=3, slack=14.0, wl=(3.0, 8.0), claim=0.0),
    "group-C": dict(prio=2, slack=30.0, wl=(6.0, 14.0), claim=0.80),
}


def make_scarcity_cluster():
    return [
        Worker("node-gpu", 4.0, 2, 3.00, ("gpu",)),
        Worker("node-cpu", 1.8, 4, 2.00, ("gpu", "cpu")),
    ]


def make_gpu_jobs(n: int = 106, seed: int = 1, span: int = 46) -> list[Job]:
    """Заявки трёх групп. Группа C систематически завышает заявленный приоритет."""
    rng = random.Random(seed)
    jobs = []
    clients = list(CONTRACT_PRIORITY)
    for i in range(n):
        client = clients[i % 3]
        p = PROFILE[client]
        wl = max(p["wl"][0], min(p["wl"][1], rng.lognormvariate(1.35, 0.35)))
        submit = int(round(i * span / max(1, n - 1))) + rng.randint(0, 1)
        prio = p["prio"]
        claim = 5 if rng.random() < p["claim"] else prio
        jobs.append(Job(
            jid=f"G{i:03d}", client=client, workload=round(wl, 1),
            deadline=submit + int(p["slack"]), submit=submit, priority=prio,
            kind="gpu", max_price=round(4.0 * wl, 1), claim_priority=claim,
        ))
    jobs.sort(key=lambda j: j.submit)
    return jobs


class Scarcity(Mechanism):
    name = "Дефицитный GPU-ресурс"
    key = "scarcity"

    def __init__(self, mode: str = "full", limit: int = 4, rounds: int = 3):
        self.mode = mode
        self.limit = limit
        self.rounds = rounds
        self.reputation = {}
        self.quota = dict(CONTRACT_QUOTA)
        self.conflicts = 0
        self.arbitrations = 0
        self.sanctions = 0
        self.rerouted = 0
        self.admitted = 0
        self.rounds_used = []
        self.accepted_price = 0.0
        self.deviations = {}

    def attach(self, sim):
        super().attach(sim)
        self.reputation = {c: 1.0 for c in set(j.client for j in sim.jobs)}
        self.gpu = sim.workers["node-gpu"]
        self.cpu = sim.workers["node-cpu"]
        self.quota = dict(CONTRACT_QUOTA) if not sim.quota else dict(sim.quota)
        self.wait_until = {}
        self.sanctioned = set()
        self.deviations = {}
        for c in self.quota:
            self.deviations[c] = 0.0

    # ---------------------------------------------------------------- утилиты
    def _depth(self, sim):
        """Сколько заданий уже стоят в очереди GPU-узла на будущее."""
        return sum(1 for j in sim.jobs if j.worker == self.gpu.wid
                   and j.status == "assigned" and (j.start or 0) > sim.t)

    def _admit(self, sim, j, protocol="simple"):
        price = self.gpu.price * j.workload
        if protocol == "simple":
            sim.bus.send(sim.t, "broker", self.gpu.wid, "request", j.jid)
            sim.bus.send(sim.t, self.gpu.wid, "broker", "accept-proposal", j.jid)
        sim.commit(self.gpu, j, price=price)
        self.admitted += 1
        self.accepted_price += price

    def _reroute(self, sim, j):
        """Отказ на GPU → задание уходит на резервный CPU-узел."""
        if sim.availability(self.cpu) <= 0:
            sim.keep_pending(j)
            return False
        sim.bus.send(sim.t, "broker", self.cpu.wid, "request", j.jid)
        sim.bus.send(sim.t, self.cpu.wid, "broker", "accept-proposal", j.jid)
        sim.commit(self.cpu, j, price=self.cpu.price * j.workload)
        self.rerouted += 1
        return True

    # -------------------------------------------------------------- режимы
    def dispatch(self, sim):
        if self.mode == "fcfs":
            for j in self.order([x for x in sim.pending]):
                self._admit(sim, j)
            return

        free = max(0, self.limit - self._depth(sim))
        waiting = [j for j in self.order(sim.pending)
                   if self.wait_until.get(j.jid, 0) <= sim.t]
        if not waiting:
            return

        if free == 0:
            # слотов нет: задания, исчерпавшие время ожидания, уходят на резерв
            for j in waiting:
                if self._must_leave(sim, j):
                    self._leave(sim, j)
                else:
                    self.wait_until[j.jid] = sim.t + 1
            return

        if len(waiting) <= free:
            for j in waiting:
                self._admit(sim, j)
            return

        self.conflicts += 1
        chosen, losers = self._bargain(sim, waiting, free)
        for j in chosen:
            self._admit(sim, j, protocol="negotiated")
        for j in losers:
            if self._must_leave(sim, j):
                self._leave(sim, j)
            else:
                self.wait_until[j.jid] = sim.t + 2
        # догружаем освободившиеся слоты GPU заданиями, которые не влезли
        for j in [x for x in losers if x.status == "pending"]:
            if self._depth(sim) < self.limit:
                self._admit(sim, j, protocol="simple")

    # --------------------------------------------------- уход на резервный узел
    def _must_leave(self, sim, j) -> bool:
        """Пора ли отдавать задание резервному узлу, чтобы успеть к сроку."""
        cpu_dur = j.duration(self.cpu.speed)
        return sim.t >= j.deadline - cpu_dur

    def _leave(self, sim, j):
        if not self._reroute(sim, j):
            self.wait_until[j.jid] = sim.t + 1

    def _bargain(self, sim, jobs, free):
        R = self.rounds
        floor = self.gpu.price
        offers = {}
        for j in jobs:
            sim.bus.send(sim.t, "broker", self.gpu.wid, "cfp", j.jid)
            sim.bus.send(sim.t, self.gpu.wid, "broker", "ask-price", j.jid,
                         floor=round(floor * j.workload, 2))
            offers[j.jid] = 0.90 * floor * j.workload
        spent = 0
        for r in range(1, R + 1):
            spent = r
            for j in jobs:
                cap = j.max_price
                offers[j.jid] += 0.30 * max(0.0, cap - offers[j.jid])
                sim.bus.send(sim.t, j.client, self.gpu.wid, "offer", j.jid,
                             price=round(offers[j.jid], 2), round=r)
            ranked = sorted(jobs, key=lambda j: -self._score(sim, j, offers[j.jid]))
            # уступки прекращаются, если разрыв в оценках устойчив
            if len(ranked) > free and len(ranked) > free:
                gap = self._score(sim, ranked[free - 1], offers[ranked[free - 1].jid]) \
                    - self._score(sim, ranked[free], offers[ranked[free].jid])
                if gap > 0.08 and r >= 2:
                    break

        self.rounds_used.append(spent)
        ranked = sorted(jobs, key=lambda j: -self._score(sim, j, offers[j.jid]))
        chosen, rest = self._select(sim, ranked, free)

        # арбитраж: близкие оценки разрешает арбитр по контрактному правилу
        if free > 0 and rest:
            s_last = self._score(sim, chosen[-1], offers[chosen[-1].jid])
            s_next = self._score(sim, rest[0], offers[rest[0].jid])
            if abs(s_last - s_next) < 0.02 and self.mode == "full":
                self.arbitrations += 1
                sim.bus.send(sim.t, "broker", "arbiter", "request-arbitration",
                             chosen[-1].jid, rival=rest[0].jid)
                pair = [chosen[-1], rest[0]]
                winner = max(pair, key=lambda j: (CONTRACT_PRIORITY[j.client],
                                                  self.reputation[j.client],
                                                  -j.submit))
                sim.bus.send(sim.t, "arbiter", "broker", "arbitration-result",
                             winner.jid)
                if winner is rest[0]:
                    chosen[-1] = rest[0]
                    rest[0] = pair[0]
                    chosen.sort(key=lambda j: -self._score(sim, j, offers[j.jid]))
                    rest.sort(key=lambda j: -self._score(sim, j, offers[j.jid]))

        for j in chosen:
            sim.bus.send(sim.t, self.gpu.wid, j.client, "accept-proposal", j.jid,
                         price=round(offers[j.jid], 2))
            sim.event("agree", f"{j.client}/{j.jid}: согласовано, цена "
                               f"{offers[j.jid]:.1f}, раунд {spent}")
        for j in rest:
            sim.bus.send(sim.t, self.gpu.wid, j.client, "reject-proposal", j.jid,
                         reason="дефицит слотов")
            self._check_claim(sim, j)
        return chosen, rest

    # ---------------------------------------------------- оценка и приоритеты
    def _select(self, sim, ranked, free):
        """Выбор победителей. В режиме full действуют квоты групп."""
        if self.mode != "full":
            return ranked[:free], ranked[free:]
        total = sum(1 for j in sim.jobs if j.worker == self.gpu.wid)
        chosen, deferred = [], []
        used = {}
        for j in ranked:
            if len(chosen) >= free:
                deferred.append(j)
                continue
            c = j.client
            taken = used.get(c, 0) + sum(1 for x in sim.jobs
                                        if x.client == c and x.worker == self.gpu.wid)
            cap = self.quota.get(c, 1.0)
            if (taken + 1) > cap * (total + len(chosen) + 1) + 1:
                deferred.append(j)
            else:
                chosen.append(j)
                used[c] = used.get(c, 0) + 1
        # свободные слоты добираем из отложенных (квота не блокирует простой)
        while len(chosen) < free and deferred:
            chosen.append(deferred.pop(0))
        return chosen, deferred

    def _priority(self, sim, j):
        if self.mode == "naive":
            return j.claim_priority or j.priority
        return min(j.priority, CONTRACT_PRIORITY[j.client])

    def _score(self, sim, j, offer):
        slack = max(0, j.deadline - sim.t)
        urgency = max(0.0, min(1.0, 1.0 - slack / 30.0))
        prio = self._priority(sim, j) / 5.0
        rep = self.reputation[j.client]
        wait = max(0.0, sim.t - j.submit) / 25.0
        bid = offer / max(1e-6, j.max_price)
        penalty = 0.0
        if self.mode == "full":
            share = self._share(sim, j.client)
            if share > self.quota.get(j.client, 1.0) + 0.02:
                penalty = 0.45
        return 0.45 * prio + 0.25 * urgency + 0.10 * rep \
            + 0.10 * min(1.0, wait) + 0.10 * bid - penalty

    def _share(self, sim, client):
        total = sum(1 for j in sim.jobs if j.worker == self.gpu.wid)
        used = sum(1 for j in sim.jobs if j.client == client and j.worker == self.gpu.wid)
        return used / max(1, total)

    def _check_claim(self, sim, j):
        """Санкция за искажение заявленного приоритета (только режим full)."""
        if self.mode != "full":
            return
        if j.claim_priority and j.claim_priority > CONTRACT_PRIORITY[j.client] \
                and j.jid not in self.sanctioned:
            self.sanctioned.add(j.jid)
            self.sanctions += 1
            self.reputation[j.client] = max(0.2, self.reputation[j.client] - 0.10)
            sim.event("sanction", f"арбитр: {j.client} заявил приоритет "
                                  f"{j.claim_priority} вместо {CONTRACT_PRIORITY[j.client]}, "
                                  f"репутация {self.reputation[j.client]:.2f}")


MODES = {
    "fcfs": "FCFS без переговоров",
    "naive": "торг без арбитража",
    "full": "торг + арбитраж + санкции",
}
