"""Механизмы распределения заданий (лабораторная работа №2).

Реализованы три механизма, каждый — в явном виде, без готовых библиотек:

1. CentralDispatch — централизованная диспетчеризация «здесь и сейчас»:
   брокер сам выбирает исполнителя, у которого в текущий момент есть
   свободный слот, и ставит задание. Очередь на будущее не резервируется.
2. ContractNet — контрактная сеть (FIPA Contract Net): рассылка cfp всем
   подходящим исполнителям, сбор предложений (propose) с оценкой момента
   завершения, выбор лучшего, подтверждение (accept-proposal / confirm).
3. ReverseAuction — обратный аукцион с двумя раундами: исполнители торгуются
   ценой, брокер минимизирует стоимость при соблюдении директивного срока.
"""
from __future__ import annotations


class Mechanism:
    name = "abstract"
    key = "base"

    def attach(self, sim):
        self.sim = sim

    # порядок рассмотрения заявок: приоритет, директивный срок, время подачи
    @staticmethod
    def order(jobs):
        return sorted(jobs, key=lambda j: (-j.priority, j.deadline, j.submit, j.jid))

    def candidates(self, job, only_free=False):
        out = []
        for w in self.sim.workers.values():
            if job.kind not in w.kinds:
                continue
            if only_free and self.sim.availability(w) <= 0:
                continue
            out.append(w)
        return out

    def dispatch(self, sim):
        raise NotImplementedError


# --------------------------------------------------------------------------
class CentralDispatch(Mechanism):
    """Централизованная диспетчеризация без резервирования на будущее."""

    name = "Централизованная диспетчеризация"
    key = "cd"

    def dispatch(self, sim):
        if not sim.pending:
            return
        for job in self.order(list(sim.pending)):
            cands = [w for w in self.candidates(job) if sim.availability(w) > 0]
            if not cands:
                continue
            # «наименее загруженный» по заявленным данным, при равенстве — дешевле
            best = max(cands, key=lambda w: (sim.availability(w), -w.price, w.wid))
            sim.bus.send(sim.t, "broker", best.wid, "request-assign", job.jid)
            start, _ = sim.commit(best, job, price=best.price * job.workload)
            sim.bus.send(sim.t, best.wid, "broker", "agree", job.jid)
            sim.event("assign", f"брокер -> {best.wid}: assign({job.jid}), старт {start}")


# --------------------------------------------------------------------------
class ContractNet(Mechanism):
    """Контрактная сеть: победитель определяется по раннему завершению."""

    name = "Контрактная сеть"
    key = "cnp"

    def dispatch(self, sim):
        if not sim.pending:
            return
        for job in self.order(list(sim.pending)):
            cands = self.candidates(job)
            if not cands:
                continue
            bids = []
            for w in cands:
                sim.bus.send(sim.t, "broker", w.wid, "cfp", job.jid)
                eft = sim.claimed_eft(w, job.duration(w.speed))
                load = w.load_factor(sim.t)
                price = w.price * job.workload * (1.0 + 0.5 * load)
                sim.bus.send(sim.t, w.wid, "broker", "propose", job.jid,
                             eft=eft, price=round(price, 2))
                bids.append((eft, price, w))
            # оценка предложений: срок выполнения первичен, цена — при равенстве
            feasible = [b for b in bids if b[0] <= job.deadline]
            eft, price, win = min(feasible or bids, key=lambda b: (b[0], b[1]))
            sim.bus.send(sim.t, "broker", win.wid, "accept-proposal", job.jid)
            sim.bus.send(sim.t, win.wid, "broker", "confirm", job.jid)
            start, finish = sim.commit(win, job, price=price)
            sim.event("assign", f"CNP: {win.wid} выиграл {job.jid} "
                                f"(eft={eft}, цена={price:.1f}, финиш={finish})")


# --------------------------------------------------------------------------
class ReverseAuction(Mechanism):
    """Обратный аукцион: минимизация цены при соблюдении директивного срока."""

    name = "Обратный аукцион"
    key = "auction"

    def dispatch(self, sim):
        if not sim.pending:
            return
        for job in self.order(list(sim.pending)):
            cands = self.candidates(job)
            if not cands:
                continue
            round1 = []
            for w in cands:
                sim.bus.send(sim.t, "broker", w.wid, "cfp", job.jid)
                load = w.load_factor(sim.t)
                price = w.price * job.workload * (0.80 + 0.60 * load)
                eft = sim.claimed_eft(w, job.duration(w.speed))
                sim.bus.send(sim.t, w.wid, "broker", "bid", job.jid,
                             price=round(price, 2), eft=eft)
                round1.append([price, eft, w])
            if not round1:
                continue

            # раунд «best-and-final»: два лидера торгов пересматривают цену
            top = sorted(round1, key=lambda r: (r[0], r[1]))[:2]
            if len(top) > 1:
                sim.bus.send(sim.t, "broker", " ".join(r[2].wid for r in top),
                             "call-for-final-offers", job.jid)
                for r in top:
                    cut = 0.90 if r[0] > job.max_price else 0.96
                    r[0] = r[0] * cut
                    sim.bus.send(sim.t, r[2].wid, "broker", "bid", job.jid,
                                 price=round(r[0], 2), final=True)

            # если лучшая цена выше предельной, брокер объявляет бюджет заказчика
            best_offer = min(r[0] for r in round1)
            if best_offer > job.max_price and len(round1) > 1:
                sim.bus.send(sim.t, "broker", "all", "call-for-bids", job.jid,
                             budget=round(job.max_price, 2))
                for r in round1:
                    r[0] = min(r[0], job.max_price * 0.95)
                    sim.bus.send(sim.t, r[2].wid, "broker", "bid", job.jid,
                                 price=round(r[0], 2), round=3)

            feasible = [r for r in round1 if r[1] <= job.deadline]
            price, eft, win = min(feasible or round1, key=lambda r: (r[0], r[1]))
            sim.bus.send(sim.t, "broker", win.wid, "award", job.jid)
            sim.bus.send(sim.t, win.wid, "broker", "confirm", job.jid)
            start, finish = sim.commit(win, job, price=price)
            sim.event("assign", f"аукцион: {win.wid} выиграл {job.jid} "
                                f"(цена={price:.1f}, финиш={finish})")


MECHANISMS = {
    "cd": CentralDispatch,
    "cnp": ContractNet,
    "auction": ReverseAuction,
}


def make(key: str) -> Mechanism:
    return MECHANISMS[key]()
