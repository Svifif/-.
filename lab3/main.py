"""Лабораторная работа №3 — переговоры и разрешение конфликтов.

Запуск: python lab3/main.py
"""
from __future__ import annotations

import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from mas.model import Sim, jain
from mas.negotiation import (CONTRACT_PRIORITY, CONTRACT_QUOTA, Scarcity,
                             make_gpu_jobs, make_scarcity_cluster)
from mas.report import Report

OUT = Path(__file__).resolve().parent / "out"
OUT.mkdir(exist_ok=True)
plt.rcParams["font.family"] = "DejaVu Sans"
plt.rcParams["legend.framealpha"] = 1.0

MODES_ORDER = ["fcfs", "naive", "full"]
LABEL = {"fcfs": "FCFS (до механизма)", "naive": "торг без арбитража",
         "full": "торг + арбитраж"}
COLORS = {"fcfs": "#c98383", "naive": "#e0b070", "full": "#8fbf8f"}
SEEDS = list(range(1, 6))
N_JOBS = 106


def max_backlog(jobs, wid="node-gpu") -> int:
    """Максимальная очередь ещё не начатых заданий на узле."""
    js = [j for j in jobs if j.worker == wid and j.start is not None]
    if not js:
        return 0
    horizon = max(j.start for j in js) + 1
    best = 0
    for t in range(horizon):
        best = max(best, sum(1 for j in js if j.assign is not None
                             and j.assign <= t < j.start))
    return best


def run_mode(mode: str, seed: int) -> dict:
    jobs = make_gpu_jobs(N_JOBS, seed=seed)
    sim = Sim(jobs, make_scarcity_cluster(), Scarcity(mode), seed=seed)
    tr = sim.run()
    m = tr.metrics()
    clients = sorted(set(j.client for j in jobs))
    gpu_total = max(1, sum(1 for j in jobs if j.worker == "node-gpu"))
    shares, sla = {}, {}
    for c in clients:
        js = [j for j in jobs if j.client == c]
        fin = [j for j in js if j.finish is not None]
        sla[c] = sum(1 for j in fin if j.finish > j.deadline) / max(1, len(js))
        shares[c] = sum(1 for j in js if j.worker == "node-gpu") / gpu_total
    mech = sim.mech
    m.update({
        "mode": mode, "seed": seed,
        "sla_by": sla, "share": shares,
        "conflicts": mech.conflicts,
        "arbitrations": mech.arbitrations,
        "sanctions": mech.sanctions,
        "rerouted": mech.rerouted,
        "rounds": st.mean(mech.rounds_used) if mech.rounds_used else 0.0,
        "backlog": max_backlog(jobs),
        "jain_access": jain([shares[c] for c in clients]),
        "quota_dev": sum(abs(shares[c] - CONTRACT_QUOTA[c]) for c in clients) / 2,
    })
    return m


def experiment() -> dict:
    out = {}
    for mode in MODES_ORDER:
        runs = [run_mode(mode, s) for s in SEEDS]
        agg = {}
        for k, v in runs[0].items():
            if k in ("mode", "seed"):
                continue
            if isinstance(v, dict):
                agg[k] = {c: st.mean(r[k][c] for r in runs) for c in v}
            else:
                agg[k] = st.mean(r[k] for r in runs)
        out[mode] = agg
    return out


def figures(res):
    clients = sorted(CONTRACT_PRIORITY)
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.2, 4.6))
    x = range(len(clients))
    w = 0.26
    for i, mode in enumerate(MODES_ORDER):
        vals = [res[mode]["sla_by"][c] * 100 for c in clients]
        ax1.bar([xx + (i - 1) * w for xx in x], vals, width=w, color=COLORS[mode],
                edgecolor="#333", linewidth=0.5, label=LABEL[mode])
    ax1.set_xticks(list(x))
    ax1.set_xticklabels([f"{c}\n(приоритет {CONTRACT_PRIORITY[c]}, "
                         f"квота {CONTRACT_QUOTA[c] * 100:.0f}%)" for c in clients],
                        fontsize=8)
    ax1.set_ylabel("Нарушения SLA, %")
    ax1.set_title("Качество обслуживания по группам", fontsize=10)
    ax1.legend(fontsize=8)
    ax1.grid(axis="y", ls=":", alpha=0.5)

    for i, mode in enumerate(MODES_ORDER):
        vals = [res[mode]["share"][c] * 100 for c in clients]
        ax2.bar([xx + (i - 1) * w for xx in x], vals, width=w, color=COLORS[mode],
                edgecolor="#333", linewidth=0.5, label=LABEL[mode])
    for xx, c in zip(x, clients):
        ax2.hlines(CONTRACT_QUOTA[c] * 100, xx - 0.42, xx + 0.42, color="#a02020",
                   ls="--", lw=1.4, label="квота" if c == clients[0] else None)
    ax2.set_xticks(list(x))
    ax2.set_xticklabels(clients, fontsize=9)
    ax2.set_ylabel("Доля слотов GPU, %")
    ax2.set_title("Фактическое распределение дефицитного ресурса", fontsize=10)
    ax2.legend(fontsize=8)
    ax2.grid(axis="y", ls=":", alpha=0.5)
    fig.suptitle("Результаты введения механизма переговоров", fontsize=12,
                 fontweight="bold")
    fig.tight_layout()
    fig.savefig(OUT / "fig3_results.png", dpi=190)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(7.6, 4.2))
    keys = [("messages", "Сообщений всего", 1.0),
            ("coord_cost", "Стоимость координации", 1.0),
            ("backlog", "Макс. очередь GPU, заданий", 1.0)]
    xs = range(len(keys))
    w = 0.26
    for i, mode in enumerate(MODES_ORDER):
        vals = [res[mode][k] * s for k, _, s in keys]
        ax.bar([xx + (i - 1) * w for xx in xs], vals, width=w, color=COLORS[mode],
               edgecolor="#333", linewidth=0.5, label=LABEL[mode])
    for xx, (k, _, _) in zip(xs, keys):
        if k == "backlog":
            ax.hlines(4, xx - 0.42, xx + 0.42, color="#a02020", ls="--", lw=1.4)
    ax.set_xticks(list(xs))
    ax.set_xticklabels([t for _, t, _ in keys], fontsize=9)
    ax.set_title("Цена координации и соблюдение ограничения на очередь", fontsize=10)
    ax.legend(fontsize=8)
    ax.grid(axis="y", ls=":", alpha=0.5)
    fig.tight_layout()
    fig.savefig(OUT / "fig3_cost.png", dpi=190)
    plt.close(fig)


def build_report(res):
    r = Report(3, "Проектирование механизма переговоров "
                  "и разрешения конфликтов")
    r.title_page()

    r.h1("1. Моделируемая ситуация конфликта")
    r.p("В системе DiSMA существует единственный GPU-узел (2 слота, "
        "производительность 4 усл. ед./тик) и резервный CPU-узел "
        "(4 слота, 1,8 усл. ед./тик, в 2,2 раза медленнее). Три группы-заказчика "
        "подают 106 заявок класса GPU за 46 тиков, то есть устойчиво больше, чем "
        "узел способен выполнить. Возникает конфликт интересов за дефицитный "
        "ресурс, осложнённый тремя обстоятельствами:")
    r.bullets([
        "группы имеют разные контракты: A — интерактивные задания с директивным "
        "сроком 6 тиков и приоритетом 4; B — бизнес-задания (14 тиков, "
        "приоритет 3); C — пакетные задания (30 тиков, приоритет 2);",
        "группа C систематически завышает заявленный приоритет до 5, чтобы "
        "получать слоты в обход контракта (80% её заявок);",
        "действует общесистемное ограничение: очередь GPU-узла не должна "
        "превышать 4 задания, иначе время ожидания становится непредсказуемым.",
    ])

    r.h1("2. Протокол переговоров и разрешения конфликтов")
    r.p("Реализованный протокол является многосторонним: за слот конкурируют "
        "несколько заказчиков, а решение принимает исполнитель под контролем "
        "арбитра. Протокол выполняется в пять шагов.")
    r.bullets([
        "Шаг 1. Заявка, не поместившаяся в очередь узла, порождает конфликт: "
        "исполнитель рассылает cfp и ask-price (цену-минимум) всем претендентам.",
        "Шаг 2. Торг: до трёх раундов. В каждом раунде заказчик повышает "
        "предложение по правилу монотонной уступки "
        "offer(r+1) = offer(r) + 0,3·(max_price − offer(r)), не выходя за бюджет.",
        "Шаг 3. Ранжирование предложений по полезности "
        "U = 0,45·приоритет + 0,25·срочность + 0,10·репутация + 0,10·ожидание + "
        "0,10·доля бюджета − штраф за превышение квоты. Побеждают предложения с "
        "наибольшей полезностью, число победителей равно числу свободных слотов.",
        "Шаг 4. Арбитраж: если полезности последнего победителя и первого "
        "проигравшего различаются менее чем на 0,02, спор передаётся арбитру, "
        "который применяет контрактное правило (приоритет группы, затем "
        "репутация, затем время подачи).",
        "Шаг 5. Отказ и санкции: проигравший получает reject-proposal и уходит "
        "на резервный узел; если заявленный приоритет превышает контрактный, "
        "арбитр фиксирует нарушение, снижает репутацию группы на 0,10 и "
        "учитывает это в дальнейших раундах.",
    ], numbered=True)
    r.p("Дополнительно исполнитель не принимает в очередь больше четырёх "
        "заданий: заявка, для которой ожидание дольше допустимого (момент "
        "подачи позже директивного срока минус время выполнения на резервном "
        "узле), принудительно отправляется на CPU-узел. Это обеспечивает "
        "соблюдение общесистемного ограничения и делает время ожидания "
        "предсказуемым.")
    r.p("Сравниваются три режима: FCFS без переговоров (базовая линия), торг "
        "без арбитража (заявленные приоритеты принимаются на веру) и полный "
        "механизм с арбитражем, квотами и санкциями.")

    r.h1("3. Результаты эксперимента")
    rows = []
    for mode in MODES_ORDER:
        d = res[mode]
        rows.append([
            LABEL[mode],
            f"{d['sla_rate'] * 100:.1f}",
            f"{d['mean_tat']:.1f}",
            f"{d['max_wait']:.0f}",
            f"{d['messages']:.0f}",
            f"{d['coord_cost']:.1f}",
            f"{d['backlog']:.1f}",
        ])
    r.table(["Режим", "SLA, %", "T обр., тик", "Макс. ожидание",
             "Сообщений", "Стоим. коорд.", "Макс. очередь GPU"],
            rows, caption="Показатели режимов распределения (среднее по 5 прогонам)",
            widths=[4.3, 1.8, 2.1, 2.3, 2.0, 2.2, 2.6], font=9)
    rows = []
    for mode in MODES_ORDER:
        d = res[mode]
        rows.append([
            LABEL[mode],
            f"{d['sla_by']['group-A'] * 100:.1f}",
            f"{d['sla_by']['group-B'] * 100:.1f}",
            f"{d['sla_by']['group-C'] * 100:.1f}",
            f"{d['share']['group-A'] * 100:.0f}",
            f"{d['share']['group-B'] * 100:.0f}",
            f"{d['share']['group-C'] * 100:.0f}",
            f"{d['quota_dev'] * 100:.1f}",
        ])
    r.table(["Режим", "SLA A, %", "SLA B, %", "SLA C, %", "Доля A, %",
             "Доля B, %", "Доля C, %", "Откл. от квот, п.п."],
            rows, caption="Показатели по группам заказчиков и соблюдение квот",
            widths=[4.0, 1.8, 1.8, 1.8, 1.7, 1.7, 1.7, 2.2], font=9)
    rows = []
    for mode in MODES_ORDER:
        d = res[mode]
        rows.append([LABEL[mode], f"{d['conflicts']:.1f}", f"{d['rounds']:.2f}",
                     f"{d['arbitrations']:.1f}", f"{d['sanctions']:.1f}",
                     f"{d['rerouted']:.1f}"])
    r.table(["Режим", "Конфликтов", "Раундов торга", "Арбитражей",
             "Санкций", "Заданий на резерв"],
            rows, caption="Параметры работы протокола переговоров",
            widths=[4.6, 2.4, 2.6, 2.4, 2.2, 3.0], font=9)
    figures(res)
    r.figure(OUT / "fig3_results.png",
             "Качество обслуживания по группам и распределение слотов GPU", 16.5)
    r.figure(OUT / "fig3_cost.png",
             "Цена координации и соблюдение ограничения на длину очереди", 14.0)

    r.h1("4. Анализ")
    f, nv, fl = res["fcfs"], res["naive"], res["full"]
    r.h2("4.1. Эффективность")
    r.p(f"Введение переговоров сокращает нарушения директивных сроков с "
        f"{f['sla_rate'] * 100:.0f}% до {fl['sla_rate'] * 100:.0f}% "
        f"(в {f['sla_rate'] / max(1e-9, fl['sla_rate']):.1f} раза), а среднее "
        f"время отклика — с {f['mean_tat']:.1f} до {fl['mean_tat']:.1f} тика. "
        f"Максимальное время ожидания падает с {f['max_wait']:.0f} до "
        f"{fl['max_wait']:.0f} тиков, максимальная очередь GPU-узла — с "
        f"{f['backlog']:.0f} до {fl['backlog']:.0f} заданий, то есть "
        f"общесистемное ограничение (не более 4) в базовой линии нарушается, а "
        f"после введения механизма соблюдается. Время полного завершения потока "
        f"изменяется с {f['makespan']:.0f} до {fl['makespan']:.0f} тиков: "
        f"механизм не ускоряет обработку, а перераспределяет её, отправляя "
        f"нетребовательные пакетные задания на резервный узел, поэтому хвост "
        f"потока определяется уже медленным CPU-узлом.")
    r.p(f"Цена механизма — рост сообщений с {f['messages']:.0f} до "
        f"{fl['messages']:.0f} (с {f['messages'] / N_JOBS:.1f} до "
        f"{fl['messages'] / N_JOBS:.1f} на задание) и стоимости координации с "
        f"{f['coord_cost']:.1f} до {fl['coord_cost']:.1f} усл. ед. Это "
        f"принципиальный компромисс: торг требует обмена предложениями, а "
        f"выигрыш по срокам достигается за счёт отказов и перенаправления "
        f"заданий, каждое из которых порождает сообщения.")
    r.h2("4.2. Справедливость и приоритезация")
    r.p(f"В базовой линии FCFS доли групп равны (по "
        f"{f['share']['group-A'] * 100:.0f}%), но справедливость формальная: "
        f"срочные интерактивные задания группы A нарушают сроки в "
        f"{f['sla_by']['group-A'] * 100:.0f}% случаев, потому что стоят в общей "
        f"очереди за пакетными заданиями группы C. Введение торга без арбитража "
        f"не решает проблемы: группа C, заявляя приоритет 5, получает "
        f"{nv['share']['group-C'] * 100:.0f}% слотов и улучшает свой показатель "
        f"до {nv['sla_by']['group-C'] * 100:.0f}%, тогда как у группы A "
        f"нарушения остаются на уровне {nv['sla_by']['group-A'] * 100:.0f}%. "
        f"Возникает эффект неблагоприятного отбора: побеждает не тот, кому "
        f"ресурс нужнее, а тот, кто громче заявляет приоритет.")
    r.p(f"Полный механизм меняет картину: санкции применяются к "
        f"{fl['sanctions']:.0f} заявкам группы C, её репутация падает, и доля "
        f"группы C снижается до {fl['share']['group-C'] * 100:.0f}% при квоте "
        f"{CONTRACT_QUOTA['group-C'] * 100:.0f}%, а нарушения SLA группы A "
        f"сокращаются до {fl['sla_by']['group-A'] * 100:.1f}% — в "
        f"{nv['sla_by']['group-A'] / max(1e-9, fl['sla_by']['group-A']):.1f} раза "
        f"по сравнению с торгом без арбитража. Суммарное отклонение фактических "
        f"долей от контрактных квот уменьшается с {nv['quota_dev'] * 100:.1f} до "
        f"{fl['quota_dev'] * 100:.1f} п.п. Справедливость здесь понимается как "
        f"соответствие распределения контрактам, а не как равенство: пакетные "
        f"задания группы C сознательно получают более медленный ресурс, но "
        f"получают его гарантированно и в рамках своей квоты.")
    r.p(f"Арбитраж потребовался в {fl['arbitrations']:.0f} конфликтах из "
        f"{fl['conflicts']:.0f} (около "
        f"{100 * fl['arbitrations'] / max(1e-9, fl['conflicts']):.0f}%): "
        f"в остальных случаях правило полезности давало однозначный результат. "
        f"Среднее число раундов торга — {fl['rounds']:.2f}, то есть уступки "
        f"прекращались, как только разрыв в оценках становился устойчивым, и "
        f"протокол не расходовал сообщения впустую.")
    r.h2("4.3. Ограничения механизма")
    r.p("Во-первых, механизм не создаёт ресурсы: при дефиците 40% заявок уходят "
        "на медленный резервный узел, и их сроки выполняются только за счёт "
        "запаса, заложенного в контракты групп B и C. Во-вторых, санкции "
        "действуют лишь на горизонте нескольких заявок: репутация снижается, но "
        "не обнуляется, поэтому злоупотребление остаётся выгодным для группы с "
        "очень большим потоком. В-третьих, торг линейно растёт по числу "
        "претендентов: при 20 одновременных заявках число сообщений на конфликт "
        "становится квадратичным, что требует ограничения числа раундов и "
        "размера конкурсной группы (в реализации — не более четырёх слотов и "
        "трёх раундов).")

    r.h1("Заключение")
    r.p("Реализован многосторонний протокол переговоров за дефицитный "
        "GPU-ресурс, включающий торг с монотонной уступкой, приоритезацию по "
        "контракту, арбитраж близких предложений, репутацию и санкции за "
        "искажение заявок, а также принудительный увод заявок на резервный узел "
        "при нарушении ограничения на длину очереди. Проведено сравнение трёх "
        f"режимов на пяти прогонах: нарушения SLA сократились с "
        f"{f['sla_rate'] * 100:.0f}% (FCFS) до {nv['sla_rate'] * 100:.0f}% "
        f"(торг без арбитража) и {fl['sla_rate'] * 100:.0f}% (полный механизм), "
        f"максимальная очередь GPU-узла — с {f['backlog']:.0f} до "
        f"{fl['backlog']:.0f} заданий, при росте сообщений в "
        f"{fl['messages'] / max(1e-9, f['messages']):.1f} раза. Показано, что без "
        f"арбитража и санкций торг сам по себе усиливает неравенство: группа, "
        f"завышающая приоритет, получает до {nv['share']['group-C'] * 100:.0f}% "
        f"дефицитного ресурса.")

    path = r.save(OUT / "ЛР3_отчет.docx")
    print("Отчёт сохранён:", path)
    return path


if __name__ == "__main__":
    data = experiment()
    for mode in MODES_ORDER:
        d = data[mode]
        print(f"{mode:5s} sla={d['sla_rate']:.3f} tat={d['mean_tat']:.1f} "
              f"maxwait={d['max_wait']:.0f} msg={d['messages']:.0f} "
              f"cc={d['coord_cost']:.1f} backlog={d['backlog']:.1f} "
              f"ms={d['makespan']:.0f} arb={d['arbitrations']:.1f} "
              f"san={d['sanctions']:.1f} rer={d['rerouted']:.1f} "
              f"rounds={d['rounds']:.2f} qdev={d['quota_dev']:.3f} "
              f"slaA={d['sla_by']['group-A']:.2f} slaC={d['sla_by']['group-C']:.2f}")
    build_report(data)
