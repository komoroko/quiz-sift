"""`html.parser` で HTML を要素の木にする(R-2)。抽出規則はこの木の上で評価する。

ブラウザが返す `outerHTML` は閉じ忘れを含みうるので、終了タグは開いている同名の要素まで遡って
閉じ、対応する開始タグが無い終了タグは捨てる。void 要素(`<br>` など)は子を持たない。
"""

from collections.abc import Iterator
from dataclasses import dataclass, field
from html.parser import HTMLParser

_VOID_ELEMENTS = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
)


@dataclass(eq=False, repr=False)
class Element:
    """要素1つ。`path` は XPath 風の位置(`/html[1]/body[1]/div[2]`)で、根拠の記録に使う。"""

    tag: str
    attrs: dict[str, str]
    path: str
    children: list["Element | str"] = field(default_factory=list)

    @property
    def classes(self) -> frozenset[str]:
        return frozenset(self.attrs.get("class", "").split())

    def iter(self) -> Iterator["Element"]:
        """自分を含む子孫要素を文書順に返す。"""
        yield self
        for child in self.children:
            if isinstance(child, Element):
                yield from child.iter()

    def find_all(self, attr: str, value: str) -> list["Element"]:
        """自分より下で属性 `attr` が `value` の要素を文書順に返す。自分自身は含めない。"""
        return [element for element in self.iter() if element is not self and element.attrs.get(attr) == value]

    def text(self) -> str:
        """子孫のテキストを連結し、空白を1つに畳んだもの。"""
        return " ".join("".join(self._texts()).split())

    def _texts(self) -> Iterator[str]:
        for child in self.children:
            if isinstance(child, str):
                yield child
            else:
                yield " " if child.tag == "br" else ""
                yield from child._texts()


def parse_html(html: str) -> Element:
    """HTML 文字列を木にして、仮想の根要素を返す。"""
    builder = _TreeBuilder()
    builder.feed(html)
    builder.close()
    return builder.root


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Element(tag="#document", attrs={}, path="")
        self._stack = [self.root]
        self._counts: list[dict[str, int]] = [{}]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        parent = self._stack[-1]
        counts = self._counts[-1]
        counts[tag] = counts.get(tag, 0) + 1
        element = Element(
            tag=tag,
            attrs={name: value or "" for name, value in attrs},
            path=f"{parent.path}/{tag}[{counts[tag]}]",
        )
        parent.children.append(element)
        if tag not in _VOID_ELEMENTS:
            self._stack.append(element)
            self._counts.append({})

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in _VOID_ELEMENTS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        for depth in range(len(self._stack) - 1, 0, -1):
            if self._stack[depth].tag == tag:
                del self._stack[depth:]
                del self._counts[depth:]
                return

    def handle_data(self, data: str) -> None:
        self._stack[-1].children.append(data)
