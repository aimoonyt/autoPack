"""Экраны в формате Rich Messages (Bot API 10.2+), как посты BLUEBLACK.

Текст экрана пишется построчно с простой разметкой:

    # Заголовок            — заголовок в плашке по центру (можно {big:СЛОВО})
    [[                     — начало плашки: строки внутри по центру
    ...
    ]]                     — конец плашки
    (баннер всегда идёт самым первым блоком; строка [banner] просто пропускается)
    ---                    — разделитель
    > текст                — цитата
    ~ текст                — мелкая подпись снизу
    остальное              — обычный текст

Внутри строк работает HTML Telegram (<b>, <i>, <u>, <s>, <code>, <a href>, <tg-emoji>)
и плейсхолдеры {ic:слот}, {big:СЛОВО}, {xm}, {name}, {price} и т.д.
"""
import html
import re
from html.parser import HTMLParser

from aiogram.types import (
    InputMediaPhoto,
    InputRichBlockBlockQuotation,
    InputRichBlockDivider,
    InputRichBlockFooter,
    InputRichBlockParagraph,
    InputRichBlockPhoto,
    InputRichBlockTable,
    InputRichMessage,
    RichBlockTableCell,
    RichTextBold,
    RichTextCode,
    RichTextCustomEmoji,
    RichTextItalic,
    RichTextSpoiler,
    RichTextStrikethrough,
    RichTextUnderline,
    RichTextUrl,
)


class _InlineParser(HTMLParser):
    """HTML Telegram -> дерево RichText."""

    WRAP = {
        "b": RichTextBold, "strong": RichTextBold,
        "i": RichTextItalic, "em": RichTextItalic,
        "u": RichTextUnderline, "ins": RichTextUnderline,
        "s": RichTextStrikethrough, "strike": RichTextStrikethrough, "del": RichTextStrikethrough,
        "code": RichTextCode,
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, dict, list]] = [("root", {}, [])]

    def handle_starttag(self, tag, attrs):
        if tag == "br":
            self.stack[-1][2].append("\n")
            return
        self.stack.append((tag, dict(attrs), []))

    def handle_endtag(self, tag):
        # закрываем до совпадающего тега (кривой HTML не роняет разбор)
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i][0] == tag:
                while len(self.stack) > i:
                    self._close()
                return

    def handle_data(self, data):
        if data:
            self.stack[-1][2].append(data)

    def _close(self):
        tag, attrs, children = self.stack.pop()
        content = _simplify(children)
        parent = self.stack[-1][2]
        if content in ("", []) and tag != "tg-emoji":
            return
        if tag in self.WRAP:
            parent.append(self.WRAP[tag](text=content))
        elif tag == "a" and attrs.get("href"):
            parent.append(RichTextUrl(text=content, url=attrs["href"]))
        elif tag == "tg-emoji" and attrs.get("emoji-id"):
            alt = "".join(c for c in children if isinstance(c, str)) or "⭐"
            parent.append(RichTextCustomEmoji(custom_emoji_id=attrs["emoji-id"], alternative_text=alt))
        elif tag == "span" and "tg-spoiler" in (attrs.get("class") or ""):
            parent.append(RichTextSpoiler(text=content))
        else:  # blockquote/pre/неизвестное — просто содержимое
            parent.extend(children)

    def result(self):
        while len(self.stack) > 1:
            self._close()
        return _simplify(self.stack[0][2])


def _simplify(items: list):
    merged: list = []
    for it in items:
        if isinstance(it, str) and merged and isinstance(merged[-1], str):
            merged[-1] += it
        else:
            merged.append(it)
    if not merged:
        return ""
    if len(merged) == 1:
        return merged[0]
    return merged


def inline(text: str):
    p = _InlineParser()
    p.feed(text)
    p.close()
    return p.result()


def _cell(text: str, header: bool = False) -> RichBlockTableCell:
    return RichBlockTableCell(text=inline(text), align="center", valign="middle", is_header=header or None)


def _box(lines: list[str], header: bool = False) -> InputRichBlockTable:
    """Плашка с текстом по центру — таблица из одной ячейки."""
    return InputRichBlockTable(cells=[[_cell("\n".join(lines), header)]], is_bordered=True)


def _blockquotes_to_boxes(text: str) -> str:
    """Пересланные посты приходят с <blockquote> — превращаем их в плашки."""
    return re.sub(
        r"<blockquote(?: expandable)?>(.*?)</blockquote>",
        lambda m: "\n[[\n" + m.group(1).strip("\n") + "\n]]\n",
        text, flags=re.S,
    )


def build(text: str, photo_file_id: str | None) -> InputRichMessage:
    """Уже отрендеренный текст экрана (плейсхолдеры подставлены) -> InputRichMessage."""
    blocks: list = []
    para: list[str] = []
    box: list[str] | None = None
    banner_used = False

    def flush_para():
        if para:
            blocks.append(InputRichBlockParagraph(text=inline("\n".join(para))))
            para.clear()

    def banner():
        nonlocal banner_used
        if photo_file_id and not banner_used:
            blocks.append(InputRichBlockPhoto(photo=InputMediaPhoto(media=photo_file_id)))
        banner_used = True

    banner()  # картинка всегда в самом верху
    for raw in _blockquotes_to_boxes(text).split("\n"):
        line = raw.rstrip()
        stripped = line.strip()
        if box is not None:
            if stripped == "]]":
                if box:
                    blocks.append(_box(box))
                box = None
            else:
                box.append(line)
            continue
        if stripped == "[[":
            flush_para()
            box = []
        elif stripped == "[banner]":
            flush_para()
            banner()
        elif stripped == "---":
            flush_para()
            blocks.append(InputRichBlockDivider())
        elif stripped.startswith("# "):
            flush_para()
            blocks.append(_box([stripped[2:]], header=True))
        elif stripped.startswith("> "):
            flush_para()
            blocks.append(InputRichBlockBlockQuotation(blocks=[InputRichBlockParagraph(text=inline(stripped[2:]))]))
        elif stripped.startswith("~ "):
            flush_para()
            blocks.append(InputRichBlockFooter(text=inline(stripped[2:])))
        elif not stripped:
            flush_para()
        else:
            para.append(line)
    if box:
        blocks.append(_box(box))
    flush_para()
    if not blocks:
        blocks.append(InputRichBlockParagraph(text=" "))
    return InputRichMessage(blocks=blocks)


def to_classic(text: str) -> str:
    """Та же разметка -> обычный HTML, если Telegram не примет rich-сообщение."""
    out: list[str] = []
    in_box = False
    for raw in _blockquotes_to_boxes(text).split("\n"):
        s = raw.strip()
        if s == "[[":
            out.append("<blockquote>")
            in_box = True
        elif s == "]]":
            if out and out[-1].endswith("\n"):
                out[-1] = out[-1].rstrip("\n")
            out.append("</blockquote>\n")
            in_box = False
        elif s in ("[banner]", "---"):
            continue
        elif s.startswith("# "):
            out.append(f"<b>{s[2:]}</b>\n")
        elif s.startswith("> "):
            out.append(f"<blockquote>{s[2:]}</blockquote>\n")
        elif s.startswith("~ "):
            out.append(f"<i>{s[2:]}</i>\n")
        else:
            out.append(raw + "\n")
    if in_box:
        out.append("</blockquote>")
    result = "".join(out).replace("<blockquote>\n", "<blockquote>")
    return re.sub(r"\n{3,}", "\n\n", result).strip() or "​"


def plain_length(text: str) -> int:
    return len(html.unescape(re.sub(r"<[^>]+>", "", text)))
