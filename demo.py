"""Демонстрация работы МАС DiSMA в разных режимах (консольный интерфейс).

Примеры:
    python demo.py run --mode auction --profile heavy --jobs 40 --trace
    python demo.py negotiate --mode full
    python demo.py attack --id S4 --defense
    python demo.py list

Файл используется как «демонстрационный материал» к отчётам: показывает
обмен сообщениями агентов, переговоры и реакцию системы на атаку.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:                      # вывод в UTF-8, чтобы кириллица отображалась корректно
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))

import mas.malice as malice
from mas.mechanisms import MECHANISMS, make
from mas.model import Sim, generate_jobs, make_cluster
from mas.negotiation import MODES, Scarcity, make_gpu_jobs, make_scarcity_cluster


def show_metrics(m: dict) -> None:
    print(f"  заданий: {m['jobs']}, выполнено: {m['done']}, "
          f"потеряно: {m['failed']}")
    print(f"  нарушения SLA: {m['sla_rate'] * 100:.1f}%")
    print(f"  среднее время отклика: {m['mean_tat']:.1f} тика, "
          f"makespan: {m['makespan']:.0f} тиков")
    print(f"  сообщений: {m['messages']} ({m['msg_per_job']:.1f} на задание), "
          f"стоимость координации: {m['coord_cost']:.1f}")


def show_trace(sim: Sim, kinds=("submit", "assign", "agree", "reject",
                                "attack", "defense", "sanction", "done"),
               limit=18) -> None:
    print("\nТрасса обмена сообщениями:")
    shown = 0
    for t, kind, text in sim.events:
        if kind in kinds and shown < limit:
            print(f"  t={t:3d} [{kind:8s}] {text}")
            shown += 1


def cmd_list(_):
    print("Механизмы распределения:")
    for key, cls in MECHANISMS.items():
        print(f"  {key:8s} {cls.name}")
    print("Режимы переговоров:")
    for key, name in MODES.items():
        print(f"  {key:8s} {name}")
    print("Сценарии диверсионного анализа:")
    for key, cfg in malice.SCENARIOS.items():
        print(f"  {key:3s} {cfg['name']}")


def cmd_run(args):
    jobs = generate_jobs(args.jobs, seed=args.seed, profile=args.profile)
    sim = Sim(jobs, make_cluster(), make(args.mode), seed=args.seed)
    sim.run()
    print(f"Механизм: {MECHANISMS[args.mode].name}, нагрузка: {args.profile}")
    print(f"Кластер: {', '.join(f'{w.wid} ({w.capacity}×{w.speed})' for w in sim.workers.values())}")
    show_metrics(sim.metrics())
    if args.trace:
        show_trace(sim)


def cmd_negotiate(args):
    jobs = make_gpu_jobs(106, seed=args.seed)
    mech = Scarcity(args.mode)
    sim = Sim(jobs, make_scarcity_cluster(), mech, seed=args.seed)
    sim.run()
    print(f"Режим переговоров: {MODES[args.mode]}")
    print(f"Квоты: {mech.quota}")
    show_metrics(sim.metrics())
    print(f"  конфликтов: {mech.conflicts}, арбитражей: {mech.arbitrations}, "
          f"санкций: {mech.sanctions}, на резервный узел: {mech.rerouted}")
    print(f"  репутация групп: "
          f"{ {c: round(v, 2) for c, v in mech.reputation.items()} }")
    if args.trace:
        show_trace(sim, limit=14)


def cmd_attack(args):
    cfg = malice.SCENARIOS[args.id]
    m = malice.run(args.id, defense=args.defense, seed=args.seed)
    print(f"Сценарий {args.id}: {cfg['name']}")
    print(f"  атака: {cfg['attack']}")
    print(f"  защита: {cfg['defense'] if args.defense else 'отключена'}")
    show_metrics(m)
    print(f"  инцидент обнаружен: {'да' if m['detected'] else 'нет'}"
          + (f", на тике {m['detect_at']}" if m['detected'] else ""))


def main():
    p = argparse.ArgumentParser(description="Демонстрация МАС DiSMA")
    sub = p.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("run", help="распределение заданий выбранным механизмом")
    p1.add_argument("--mode", default="auction", choices=list(MECHANISMS))
    p1.add_argument("--profile", default="medium",
                    choices=["light", "medium", "heavy"])
    p1.add_argument("--jobs", type=int, default=60)
    p1.add_argument("--seed", type=int, default=1)
    p1.add_argument("--trace", action="store_true")
    p1.set_defaults(func=cmd_run)

    p2 = sub.add_parser("negotiate", help="режимы переговоров за GPU-ресурс")
    p2.add_argument("--mode", default="full", choices=list(MODES))
    p2.add_argument("--seed", type=int, default=1)
    p2.add_argument("--trace", action="store_true")
    p2.set_defaults(func=cmd_negotiate)

    p3 = sub.add_parser("attack", help="неблагоприятный сценарий")
    p3.add_argument("--id", default="S1", choices=list(malice.SCENARIOS))
    p3.add_argument("--defense", action="store_true")
    p3.add_argument("--seed", type=int, default=1)
    p3.set_defaults(func=cmd_attack)

    p4 = sub.add_parser("list", help="список режимов и сценариев")
    p4.set_defaults(func=cmd_list)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
