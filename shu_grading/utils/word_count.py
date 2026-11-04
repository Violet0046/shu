"""作文正文计数：含标点，不含标题、空白；疑字占一个字。"""
import re


def count_essay_words(body: str) -> int:
    body = re.sub(r'［疑字］|\[疑字\]', '字', body)
    return sum(1 for character in body if not character.isspace())
