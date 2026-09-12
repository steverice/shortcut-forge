"""An action list that knows the idioms Shortcuts needs.

`ActionList` is a plain `list` of action dictionaries that also carries the
UUID source, so a generator can keep appending `act(...)` dictionaries by hand
and reach for a named idiom where one exists. The idioms encode findings that
were each paid for on a device; their docstrings say which.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from shortcut_forge.plist import act, attach, comment, cond_input, out, ts

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

LESS_THAN = 0
"""`WFCondition` for "is less than". Proven on device."""

GREATER_THAN = 2
"""`WFCondition` for "is greater than". Proven on device.

These two are the only comparisons this library exports, because they are the
only ones that have been verified to branch correctly on a numeric input. A
multi-condition If imports with empty red rows; build the comparison out of
Match Text, Count, and one of these instead.
"""

CONDITIONAL = "is.workflow.actions.conditional"
REPEAT_EACH = "is.workflow.actions.repeat.each"
REPEAT_COUNT = "is.workflow.actions.repeat.count"
MENU = "is.workflow.actions.choosefrommenu"

OPEN, ELSE, CLOSE = 0, 1, 2
"""`WFControlFlowMode` values: the start marker, the middle marker, the end marker."""


class ActionList(list[dict[str, Any]]):
    """The actions of one shortcut, plus the UUID source they are minted from.

    `next(A.uuids)` and `A.uuid()` are the same thing; the iterator is exposed
    so a generator written as `i = A.uuids; next(i)` reads naturally.
    """

    def __init__(self, uuids: Iterator[str]) -> None:
        super().__init__()
        self.uuids = uuids

    def uuid(self) -> str:
        return next(self.uuids)

    def add(self, identifier: str, **params: Any) -> None:
        self.append(act(identifier, **params))

    def comment(self, text: str) -> None:
        self.append(comment(text))

    # -- values ------------------------------------------------------------
    def text(self, value: str | dict[str, Any], *, name: str | None = None) -> str:
        """A Text action. Returns its UUID; its output is named `Text` unless `name` is given.

        A literal string is written as-is; a `ts()` value is written as text
        with variables. Text is the universal adapter in Shortcuts: it is how a
        dictionary value, a stored value, or Shortcut Input becomes something
        Match Text will read.
        """
        u = self.uuid()
        params: dict[str, Any] = {"UUID": u, "WFTextActionText": value}
        if name is not None:
            params["CustomOutputName"] = name
        self.add("is.workflow.actions.gettext", **params)
        return u

    def number(self, value: str | int) -> str:
        """A Number action. Returns its UUID; the output is named `Number`."""
        u = self.uuid()
        self.add("is.workflow.actions.number", UUID=u, WFNumberActionNumber=str(value))
        return u

    def count_matches(self, source: dict[str, Any], pattern: str = r"\S", *, coerce: bool = True) -> str:
        """Text, Match Text, Count. Returns the Count action's UUID; read it as `Count`.

        This is how a value becomes something an If can branch on. Two
        Shortcuts behaviors force it: a dictionary value compared directly in
        an If reads as blank and the branch never fires, and an empty string
        still satisfies "has any value", so presence has to be measured rather
        than tested. Counting matches handles both, and a pattern turns the
        same three actions into "does this value say X".

        `source` is an attachment value: `out(uuid, name)`, `var(name)`, or
        `EXTENSION_INPUT`. With `coerce=False` the Text action is skipped and
        Match Text reads `source` directly; that is proven for an output that
        is already text, and the extra Text is what makes a dictionary value,
        a stored value, or Shortcut Input readable at all.
        """
        subject = source
        if coerce:
            t = self.uuid()
            self.add("is.workflow.actions.gettext", UUID=t, WFTextActionText=ts(source))
            subject = out(t, "Text")
        m, c = self.uuid(), self.uuid()
        self.add("is.workflow.actions.text.match", UUID=m, WFMatchTextPattern=pattern, text=ts(subject))
        # Both `WFInput` and `Input` are set. An export carries both and the
        # validator asks for both; leaving one out has not been tried on a device.
        self.add(
            "is.workflow.actions.count",
            UUID=c,
            WFCountType="Items",
            WFInput=attach(out(m, "Matches")),
            Input=attach(out(m, "Matches")),
        )
        return c

    # -- control flow ------------------------------------------------------
    #
    # Every block is three markers sharing a GroupingIdentifier, and the close
    # marker must carry the same action identifier as the open one. A Repeat
    # closed by a conditional imports without complaint and its body never
    # runs; `checks.check_control_flow` refuses that shape, and these helpers
    # cannot produce it.
    def if_open(self, group: str, *, condition: int, number: str | int, source: dict[str, Any]) -> None:
        """Start an If comparing `source` (a numeric output) against a literal `number`."""
        self.add(
            CONDITIONAL,
            UUID=self.uuid(),
            GroupingIdentifier=group,
            WFControlFlowMode=OPEN,
            WFCondition=condition,
            WFNumberValue=str(number),
            WFInput=cond_input(source),
        )

    def if_else(self, group: str) -> None:
        self.add(CONDITIONAL, UUID=self.uuid(), GroupingIdentifier=group, WFControlFlowMode=ELSE)

    def if_close(self, group: str) -> None:
        self.add(CONDITIONAL, UUID=self.uuid(), GroupingIdentifier=group, WFControlFlowMode=CLOSE)

    def repeat_each_open(self, group: str, source: dict[str, Any]) -> None:
        """Start a Repeat with Each over `source`, an attachment value that is a list.

        Inside, the item is `Repeat Item`, unless this loop is nested in
        another Repeat of either kind, in which case it is `Repeat Item 2` and
        so on. Getting that wrong reads as an empty item, silently.
        """
        self.add(
            REPEAT_EACH, UUID=self.uuid(), GroupingIdentifier=group, WFControlFlowMode=OPEN, WFInput=attach(source)
        )

    def repeat_each_close(self, group: str) -> None:
        self.add(REPEAT_EACH, UUID=self.uuid(), GroupingIdentifier=group, WFControlFlowMode=CLOSE)

    def repeat_count_open(self, group: str, count: int) -> None:
        self.add(REPEAT_COUNT, UUID=self.uuid(), GroupingIdentifier=group, WFControlFlowMode=OPEN, WFRepeatCount=count)

    def repeat_count_close(self, group: str) -> None:
        self.add(REPEAT_COUNT, UUID=self.uuid(), GroupingIdentifier=group, WFControlFlowMode=CLOSE)

    def menu_open(self, group: str, prompt: str, items: Sequence[str]) -> None:
        """Start a Choose from Menu. Each item needs exactly one `menu_case` with the same title."""
        self.add(
            MENU,
            UUID=self.uuid(),
            GroupingIdentifier=group,
            WFControlFlowMode=OPEN,
            WFMenuPrompt=prompt,
            WFMenuItems=list(items),
        )

    def menu_case(self, group: str, title: str) -> None:
        self.add(MENU, UUID=self.uuid(), GroupingIdentifier=group, WFControlFlowMode=ELSE, WFMenuItemTitle=title)

    def menu_close(self, group: str) -> None:
        self.add(MENU, UUID=self.uuid(), GroupingIdentifier=group, WFControlFlowMode=CLOSE)

    # -- endings -----------------------------------------------------------
    def notify(self, title: dict[str, Any] | None = None, body: dict[str, Any] | None = None) -> None:
        """Show Notification. `title` and `body` are `ts()` values."""
        params: dict[str, Any] = {}
        if title is not None:
            params["WFNotificationActionTitle"] = title
        if body is not None:
            params["WFNotificationActionBody"] = body
        self.add("is.workflow.actions.notification", **params)

    def exit(self) -> None:
        """Stop the shortcut. Only proven at the top level, outside every Repeat."""
        self.add("is.workflow.actions.exit")
