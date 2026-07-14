"""不依赖 LLM 的固定中文检索语料。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BenchmarkDocument:
    """一条可同时写入 FTS 与向量索引的记忆。"""

    id: str
    text: str


@dataclass(frozen=True)
class BenchmarkQuery:
    """固定查询及其相关文档。"""

    id: str
    text: str
    expected_ids: tuple[str, ...]
    category: str


_DOCUMENTS = (
    BenchmarkDocument("pet-name", "用户养了一只叫汤圆的英国短毛猫，今年三岁。"),
    BenchmarkDocument("food-taboo", "用户吃饭不放香菜，也不喜欢芹菜的味道。"),
    BenchmarkDocument("work-city", "用户目前住在绍兴，每周坐高铁去杭州上班。"),
    BenchmarkDocument("weekend-sport", "用户通常在周六清晨沿江跑五公里。"),
    BenchmarkDocument("music", "用户喜欢爵士乐，尤其常听 Miles Davis 的专辑。"),
    BenchmarkDocument("brother-device", "弟弟张小明最近用电子墨水阅读器学习英语。"),
    BenchmarkDocument("anniversary", "用户和伴侣把每年十月十六日当作纪念日。"),
    BenchmarkDocument("coffee", "用户上午习惯喝不加糖的冰美式，下午避免咖啡因。"),
    BenchmarkDocument("project", "用户正在做一个叫 agent-friend 的桌面 AI 朋友项目。"),
    BenchmarkDocument("travel", "用户计划秋天去京都看红叶，并想住在岚山附近。"),
    BenchmarkDocument("book", "用户最近在读《献给阿尔吉侬的花束》，觉得后半段很难过。"),
    BenchmarkDocument("meeting", "用户每周三下午四点参加产品例会。"),
)

_QUERIES = (
    BenchmarkQuery("pet-exact", "汤圆 英短猫", ("pet-name",), "keyword"),
    BenchmarkQuery("pet-paraphrase", "用户家里的宠物叫什么名字？", ("pet-name",), "paraphrase"),
    BenchmarkQuery("food-exact", "香菜 芹菜", ("food-taboo",), "keyword"),
    BenchmarkQuery("food-paraphrase", "给用户做饭要避开什么配菜？", ("food-taboo",), "paraphrase"),
    BenchmarkQuery("work-exact", "杭州上班", ("work-city",), "keyword"),
    BenchmarkQuery("work-paraphrase", "用户通勤去哪个城市工作？", ("work-city",), "paraphrase"),
    BenchmarkQuery("sport-exact", "周六 跑五公里", ("weekend-sport",), "keyword"),
    BenchmarkQuery(
        "sport-paraphrase", "用户周末通常做什么运动？", ("weekend-sport",), "paraphrase"
    ),
    BenchmarkQuery("device-exact", "张小明 电子墨水", ("brother-device",), "keyword"),
    BenchmarkQuery(
        "device-paraphrase",
        "弟弟通过什么设备学习外语？",
        ("brother-device",),
        "paraphrase",
    ),
    BenchmarkQuery("coffee-exact", "冰美式 不加糖", ("coffee",), "keyword"),
    BenchmarkQuery("coffee-paraphrase", "用户下午为什么不喝咖啡？", ("coffee",), "paraphrase"),
    BenchmarkQuery("travel-exact", "京都 岚山 红叶", ("travel",), "keyword"),
    BenchmarkQuery("travel-paraphrase", "秋季旅行想住在什么地方附近？", ("travel",), "paraphrase"),
    BenchmarkQuery("meeting-exact", "周三 四点 产品例会", ("meeting",), "keyword"),
    BenchmarkQuery(
        "meeting-paraphrase", "固定工作会议安排在什么时候？", ("meeting",), "paraphrase"
    ),
)


def benchmark_documents(corpus_size: int = 1000) -> list[BenchmarkDocument]:
    """返回固定目标文档，加确定性的无关噪声直到 ``corpus_size``。"""
    if corpus_size < len(_DOCUMENTS):
        raise ValueError(f"corpus_size 至少为 {len(_DOCUMENTS)}")
    documents = list(_DOCUMENTS)
    topics = ("园艺", "烘焙", "摄影", "木工", "游泳", "天文", "书法", "陶艺")
    places = ("成都", "青岛", "昆明", "苏州", "厦门", "长沙", "大连", "无锡")
    schedules = ("周一上午", "周二晚上", "周四下午", "周五中午", "周日傍晚")
    for index in range(corpus_size - len(_DOCUMENTS)):
        topic = topics[index % len(topics)]
        place = places[(index // len(topics)) % len(places)]
        schedule = schedules[(index // (len(topics) * len(places))) % len(schedules)]
        documents.append(
            BenchmarkDocument(
                id=f"noise-{index:05d}",
                text=f"编号{index:05d}的虚构样本记录：某位参与者在{place}于{schedule}练习{topic}。",
            )
        )
    return documents


def benchmark_queries() -> list[BenchmarkQuery]:
    """返回稳定顺序的查询集合。"""
    return list(_QUERIES)
