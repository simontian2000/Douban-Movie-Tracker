import os
import re
import json
import hashlib
import logging
from pathlib import Path
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import feedparser
import requests
from bs4 import BeautifulSoup

RSS_URL = os.environ["DOUBAN_RSS_URL"]
WEBHOOK = os.environ["DISCORD_WEBHOOK_URL"]

STATE_FILE = Path("data/sent_records.json")
STATE_FILE.parent.mkdir(exist_ok=True)

STATUSES = {
    "看过": "已看过",
    "想看": "想看",
    "在看": "正在看",
}

RATINGS = {
    "力荐": 5,
    "推荐": 4,
    "还行": 3,
    "较差": 2,
    "很差": 1,
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 DoubanDiscordSync/1.0"
})


def load_state():
    if not STATE_FILE.exists():
        return {"initialized": False, "sent": []}

    try:
        return json.loads(STATE_FILE.read_text("utf-8"))
    except Exception:
        raise RuntimeError("状态文件损坏，停止运行以防重复推送")


def save_state(state):
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def get_text(soup):
    return soup.get_text("\n", strip=True)


def parse_date(entry):
    value = entry.get("published", "")

    try:
        dt = parsedate_to_datetime(value)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return value[:10] if value else ""


def parse_entry(entry):
    link = entry.get("link", "")

    if "movie.douban.com/subject/" not in link:
        return None

    raw_title = BeautifulSoup(
        entry.get("title", ""),
        "html.parser"
    ).get_text(strip=True)

    status = None
    title = raw_title

    for prefix, label in STATUSES.items():
        if raw_title.startswith(prefix):
            status = label
            title = raw_title[len(prefix):].strip()
            break

    if not status or not title:
        return None

    html = (
        entry.get("summary", "")
        or entry.get("description", "")
    )

    if entry.get("content"):
        html += " " + " ".join(
            item.get("value", "")
            for item in entry["content"]
        )

    soup = BeautifulSoup(html, "html.parser")
    text = get_text(soup)

    image = soup.find("img")
    poster = image.get("src", "") if image else ""

    rating = None
    for word, score in RATINGS.items():
        if re.search(rf"(?:推荐|评分)[：:]\s*{word}", text):
            rating = score
            break

    comment = ""
    match = re.search(
        r"备注[：:]\s*(.*)",
        text,
        re.S
    )

    if match:
        comment = match.group(1).strip()
        comment = re.split(
            r"\n(?:推荐|评分|标签)[：:]",
            comment
        )[0].strip()

    # 唯一标识使用条目链接 + 状态 + 日期
    date = parse_date(entry)
    identity = f"{link}|{status}|{date}"

    record_id = hashlib.sha256(
        identity.encode("utf-8")
    ).hexdigest()

    return {
        "id": record_id,
        "title": title,
        "status": status,
        "link": link,
        "date": date,
        "rating": rating,
        "comment": comment,
        "poster": poster,
    }


def download_poster(url):
    if not url:
        return None

    try:
        response = session.get(
            url,
            headers={"Referer": "https://movie.douban.com/"},
            timeout=12
        )
        response.raise_for_status()

        content_type = response.headers.get(
            "Content-Type", ""
        )

        if not content_type.startswith("image/"):
            return None

        if len(response.content) > 8 * 1024 * 1024:
            return None

        return response.content

    except Exception as error:
        logging.warning("海报下载失败: %s", error)
        return None


def send_discord(item):
    fields = [
        {
            "name": "状态",
            "value": item["status"],
            "inline": True
        },
        {
            "name": "标记日期",
            "value": item["date"] or "未知",
            "inline": True
        }
    ]

    if item["rating"] is not None:
        fields.append({
            "name": "评分",
            "value": (
                "★" * item["rating"]
                + "☆" * (5 - item["rating"])
            ),
            "inline": True
        })

    embed = {
        "title": item["title"],
        "url": item["link"],
        "color": 0x5865F2,
        "fields": fields,
        "footer": {"text": "Douban Movie Tracker"},
    }

    if item["comment"]:
        embed["description"] = (
            "**我的短评**\n"
            + item["comment"][:3500]
        )

    image_data = download_poster(item["poster"])

    if image_data:
        embed["image"] = {
            "url": "attachment://poster.jpg"
        }
        payload = {"embeds": [embed]}

        response = session.post(
            WEBHOOK,
            data={"payload_json": json.dumps(payload)},
            files={
                "files[0]": (
                    "poster.jpg",
                    image_data,
                    "image/jpeg"
                )
            },
            timeout=20
        )
    else:
        payload = {"embeds": [embed]}
        response = session.post(
            WEBHOOK,
            json=payload,
            timeout=20
        )

    response.raise_for_status()


def main():
    logging.info("开始检查豆瓣 RSS")

    feed = feedparser.parse(RSS_URL)

    if feed.bozo and not feed.entries:
        raise RuntimeError(
            f"RSS 解析失败: {feed.bozo_exception}"
        )

    if not feed.entries:
        raise RuntimeError(
            "RSS 没有返回任何条目，请检查订阅地址或豆瓣访问限制"
        )

    items = []

    for entry in feed.entries:
        try:
            item = parse_entry(entry)
            if item:
                items.append(item)
        except Exception:
            logging.exception("单条记录解析失败")

    state = load_state()
    known = set(state["sent"])

    logging.info(
        "RSS 返回 %s 条，识别电影记录 %s 条",
        len(feed.entries),
        len(items)
    )

    if not state["initialized"]:
        state["sent"] = list(
            dict.fromkeys(item["id"] for item in items)
        )
        state["initialized"] = True
        save_state(state)

        logging.info(
            "首次运行：已建立历史基线，不推送旧记录"
        )
        return

    new_items = [
        item for item in items
        if item["id"] not in known
    ]

    # RSS 通常按时间倒序，发送时先发旧的
    new_items.reverse()

    for item in new_items:
        logging.info(
            "准备发送: %s (%s)",
            item["title"],
            item["status"]
        )

        send_discord(item)

        state["sent"].append(item["id"])
        state["sent"] = list(dict.fromkeys(state["sent"]))
        save_state(state)

        logging.info("推送成功: %s", item["title"])

    logging.info("本次完成，新增 %s 条", len(new_items))


if __name__ == "__main__":
    main()
