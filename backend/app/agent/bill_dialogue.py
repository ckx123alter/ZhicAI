"""Evidence-based page insights, also available if prose generation is unavailable."""


def bill_bubble_segments(result):
    insights = result['insights']
    ranked = insights['categories_ranked']
    if not ranked:
        return [f"{result['month']}没有消费记录，可以换一个有账单的月份看看。",
                '点击闪烁可以查看消费明细。']
    top = ranked[0]
    structure = f"{result['month']}花得最多的是{top['name']}，占这月支出的{top['share_pct']:.2f}%。"
    if len(ranked) > 1:
        structure += f"加上{ranked[1]['name']}，两类合计占{insights['top_two_share_pct']:.2f}%。想控制预算，可以先从这两类找空间。"
    else:
        structure += '这份账单只有这一类支出，可以再看看里面的商户分布。'
    structure += f"这月一共{insights['transaction_count']}笔消费，平均每笔 ¥{insights['average']:.2f}。"

    frequent = result['frequency_detail']
    if frequent['merchants']:
        habits = f"这月有{frequent['merchant_count']}个高频商户，每个商户至少出现{frequent['minimum_count']}笔消费，一共花了 ¥{frequent['total']:.2f}。"
        habits += '；'.join(f"{item['merchant']} {item['count']}笔、合计 ¥{item['amount']:.2f}" for item in frequent['merchants'][:2])
        habits += '。想少花一点，可以先挑一项重复消费设个每月额度。'
    else:
        habits = f"这月没有高频商户达到{frequent['minimum_count']}笔消费，可以先看看单笔较大的开销。"
    largest = insights['largest_transaction']
    habits += f"最大单笔是{largest['merchant']}，¥{largest['amount']:.2f}，点开消费明细就能找到这笔。"

    change = insights['month_change']
    if insights['month_change_abs_pct'] is not None:
        if change == 0:
            comparison = f"这月账单支出和{insights['previous_month']}持平。"
        else:
            direction = '多花' if change > 0 else '少花'
            comparison = (f"这月账单比{insights['previous_month']}{direction}了 ¥{insights['month_change_abs']:.2f}，"
                          f"{'增加' if change > 0 else '减少'}{insights['month_change_abs_pct']:.2f}%。")
    else:
        comparison = '上月账单支出是零，这次先看这月的消费分布。' if insights['previous_transaction_count'] else '还没有上月账单，先看这月的消费分布。'
    comparison += f"接下来可以看看{top['name']}的明细，把每月都会有的开销和偶尔的大笔支出分开，下月预算会更好安排。"
    return [structure, habits, comparison, '点击闪烁可以查看消费明细。']
