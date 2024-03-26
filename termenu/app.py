import sys
import re
import time
import functools
import signal
from textwrap import dedent
from . import termenu, keyboard
from contextlib import contextmanager, ExitStack
from . import ansi
from .colors import Colorized, uncolorize
import collections


class ParamsException(Exception):
    "An exception object that accepts arbitrary params as attributes"
    def __init__(self, message="", *args, **kwargs):
        if args:
            message %= args
        self.message = message
        for k, v in kwargs.items():
            setattr(self, k, v)
        self.params = kwargs


NoneType = type(None)

import os

DEFAULT_CONFIG = """
# WHITE<<termenu>> has created for you this default configuration file.
# You can modify it to control which glyphs are used in termenu apps, to improve the readability
# and usuability of these apps. This depends on the terminal you use.
# This could be helpful: CYAN<<http://xahlee.info/comp/unicode_geometric_shapes.html>>

SCROLL_UP_MARKER = "^"  # consider 🢁
SCROLL_DOWN_MARKER = "V"  # consider 🢃
ACTIVE_ITEM_MARKER = " WHITE@{>}@"  # consider 🞂
SELECTED_ITEM_MARKER = "WHITE@{*}@"  # consider ⚫
SELECTABLE_ITEM_MARKER = "-"  # consider ⚪
CONTINUATION_SUFFIX = "DARK_RED@{↩}@"  # for when a line overflows
CONTINUATION_PREFIX = "DARK_RED@{↪}@"  # for when a line overflows
"""

CFG_PATH = os.path.expanduser("~/.termenu/app_chars.py")
app_chars = DEFAULT_CONFIG

try:
    with open(CFG_PATH) as f:
        app_chars = f.read()
except PermissionError:
    pass
except FileNotFoundError:
    try:
        os.makedirs(os.path.dirname(CFG_PATH), exist_ok=True)
        with open(CFG_PATH, "w") as f:
            f.write(DEFAULT_CONFIG)
    except (OSError, PermissionError):
        pass


APP_CHARS = {}
eval(compile(app_chars, CFG_PATH, 'exec'), {}, APP_CHARS)


@contextmanager
def _no_resize_handler():
    handler = signal.signal(signal.SIGWINCH, signal.SIG_DFL)
    try:
        yield
    finally:
        signal.signal(signal.SIGWINCH, handler)


# ===============================================================================
# Termenu
# ===============================================================================
class TermenuAdapter(termenu.Termenu):

    class RefreshSignal(ParamsException): ...
    class TimeoutSignal(ParamsException): ...
    class HelpSignal(ParamsException): ...
    class SelectSignal(ParamsException): ...

    FILTER_SEPARATOR = ","
    FILTER_MODES = ["and", "nand", "or", "nor"]
    EMPTY = "DARK_RED<< (Empty) >>"
    SCROLL_UP_MARKER = APP_CHARS['SCROLL_UP_MARKER']
    SCROLL_DOWN_MARKER = APP_CHARS['SCROLL_DOWN_MARKER']
    ACTIVE_ITEM_MARKER = APP_CHARS['ACTIVE_ITEM_MARKER']
    SELECTED_ITEM_MARKER = APP_CHARS['SELECTED_ITEM_MARKER']
    SELECTABLE_ITEM_MARKER = APP_CHARS['SELECTABLE_ITEM_MARKER']
    CONTINUATION_SUFFIX = Colorized(APP_CHARS['CONTINUATION_SUFFIX'])
    CONTINUATION_PREFIX = Colorized(APP_CHARS['CONTINUATION_PREFIX'])
    TITLE_PAD = "  "

    class _Option(termenu.Termenu._Option):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.raw = self.text
            self.text = Colorized(self.raw)
            self.filter_text = (self.attrs.get('filter_text') or self.text.uncolored).lower()
            if isinstance(self.result, str):
                self.result = ansi.decolorize(self.result)
            self.menu = None  # will get filled up later

        @property
        def selectable(self):
            return self.attrs.get("selectable", True)

        @property
        def markable(self):
            return self.attrs.get("markable", True) and self.selectable

    def __init__(self, app):
        self.height = self.title_height = 1
        self.text = None
        self.filter_mode_idx = 0
        self.is_empty = True
        self.dirty = False
        self.timeout = (time.time() + app.timeout) if app.timeout else None
        self.app = app

    def handle_termsize_change(self, signal, frame):
        import threading
        if threading.current_thread() == threading.main_thread():
            self.refresh("signal")

    @property
    def filter_mode(self):
        return self.FILTER_MODES[self.filter_mode_idx]

    def reset(self, title="No Title", header="", selection=None, *args, height, **kwargs):

        self._highlighted = False
        remains = self.timeout and (self.timeout - time.time())
        if remains:
            fmt = "(%s<<%ds left>>)"
            if remains <= 5:
                color, fmt = "RED", "(%s<<%.1fs left>>)"
            elif remains < 11:
                color = "YELLOW"
            else:
                color = "DARK_YELLOW"
            title += fmt % (color, remains)
        if header:
            title += "\n" + header
        title = Colorized(title)
        terminal_width, terminal_height = termenu.get_terminal_size()
        if not height:
            height = terminal_height - 2  # leave a margine
        terminal_width -= len(self.TITLE_PAD)
        title_lines = []

        for line in title.splitlines():
            line = line.expandtabs()
            if len(line.uncolored) <= terminal_width:
                title_lines.append(line)
            else:
                indentation, line = re.match("(\\s*)(.*)", line).groups()
                line = Colorized(line)
                continuation_prefix = ""
                while line:
                    # we have to keep space for a possible contat the end
                    width = terminal_width - len(indentation) - len(self.CONTINUATION_SUFFIX.uncolored)
                    if continuation_prefix:
                        width -= len(continuation_prefix.uncolored)
                        line = self.CONTINUATION_PREFIX + line
                    title_lines.append(indentation + line[:width])
                    line = line[width:]
                    if line:
                        title_lines[-1] += self.CONTINUATION_SUFFIX
                    continuation_prefix = self.CONTINUATION_PREFIX

        self.title_height = len(title_lines)
        self.title = Colorized("\n".join(self.TITLE_PAD + l for l in title_lines))
        height -= self.title_height
        with self._selection_preserved(selection):
            super().__init__(*args, height=height, **kwargs)

    def _make_option_objects(self, options):
        options = super()._make_option_objects(options)
        for opt in options:
            opt.menu = self
        self._allOptions = options[:]
        return options

    def _decorate_flags(self, index):
        flags = super()._decorate_flags(index)
        flags["markable"] = self.options[self.scroll + index].attrs.get("markable", self.multiselect)
        flags['highlighted'] = self._highlighted and flags['selected']
        return flags

    def _decorate(self, option, **flags):
        "Decorate the option to be displayed"

        highlighted = flags.get("highlighted", True)
        active = flags.get("active", False)
        selected = flags.get("selected", False)
        markable = flags.get("markable", False)
        moreAbove = flags.get("moreAbove", False)
        moreBelow = flags.get("moreBelow", False)

        # add selection / cursor decorations
        option = Colorized(
            (" " if not markable else self.SELECTED_ITEM_MARKER if selected else self.SELECTABLE_ITEM_MARKER) +
            (self.ACTIVE_ITEM_MARKER if active else "  ") +
            option)
        if highlighted:
            option = ansi.colorize(option.uncolored, "cyan", bright=True)
        else:
            option = str(option)  # convert from Colorized to ansi string

        # add more above/below indicators
        marker = self.SCROLL_UP_MARKER if moreAbove else self.SCROLL_DOWN_MARKER if moreBelow else " "
        return ansi.colorize(marker, "white", bright=True) + " " + option

    @contextmanager
    def _selection_preserved(self, selection=None):
        if self.is_empty:
            yield
            return

        prev_active = self._get_active_option().result
        prev_selected = {o.result for o in self._allOptions if o.selected} if selection is None else set(selection)
        try:
            yield
        finally:
            if prev_selected:
                for option in self.options:
                    option.selected = option.result in prev_selected
            self._set_default(prev_active)

    def show(self, default=None, auto_clear=True):
        self._refilter()
        self._clear_cache()
        self._set_default(default)
        orig_handler = signal.signal(signal.SIGWINCH, self.handle_termsize_change)
        try:
            return super().show(auto_clear=auto_clear)
        finally:
            signal.signal(signal.SIGWINCH, orig_handler)

    def _set_default(self, default):
        if default is None:
            return
        for index, o in enumerate(self.options):
            if o.result == default:
                break
        else:
            return
        for i in range(index):
            self._on_down()

    def _adjust_width(self, option):
        option = Colorized("BLACK<<\\>>").join(option.splitlines())
        l = len(uncolorize(str(option)))
        w = max(self.width, 8)
        if l > w:
            option = termenu.shorten(option, w)
        if l < w:
            option += " " * (w - l)
        return option

    def _on_key(self, key):
        bubble_up = True
        if not key == "heartbeat":
            self.timeout = None
        if key == "space":
            key = " "
        elif key == "`":
            key = "insert"

        if key == "*" and self.multiselect:
            for option in self.options:
                if option.markable:
                    option.selected = not option.selected
        elif len(key) == 1 and 32 <= ord(key) <= 127:
            if key == " " and not self.text:
                pass
            else:
                if not self.text:
                    self.text = []
                self.text.append(key)
                self._refilter()
            bubble_up = False
        elif key == "enter" and self.is_empty:
            bubble_up = False
        elif key == "backspace" and self.text:
            del self.text[-1]
            self._refilter()
        elif key == "esc":
            if self.text is not None:
                filters = "".join(self.text or []).split(self.FILTER_SEPARATOR)
                if filters:
                    filters.pop(-1)
                self.text = list(self.FILTER_SEPARATOR.join(filters)) if filters else None
                if not filters:
                    self.filter_mode_idx = 0
                ansi.hide_cursor()
                bubble_up = False
                self._refilter()
            else:
                found_selected = False
                for option in self.options:
                    found_selected = found_selected or option.selected
                    option.selected = False
                bubble_up = not  found_selected
        elif key == "end":
            self._on_end()
            bubble_up = False
        elif callable(getattr(self.app, "on_%s" % key, None)):
            getattr(self.app, "on_%s" % key)(self)
            bubble_up = False

        if bubble_up:
            return super()._on_key(key)

    def _on_F5(self):
        self.refresh('user')

    def _on_F1(self):
        self.help()

    def _on_ctrlSlash(self):
        if self.text:
            self.filter_mode_idx = (self.filter_mode_idx + 1) % len(self.FILTER_MODES)
            self._refilter()

    def _on_enter(self):
        if any(option.selected for option in self.options):
            self._highlighted = True
            self._goto_top()
            self._print_menu()
            time.sleep(.1)
        elif not self._get_active_option().selectable:
            return False
        return True  # stop loop

    def _on_insert(self):
        option = self._get_active_option()
        if option.markable:
            super()._on_space()
        else:
            super()._on_down()

    def _on_end(self):
        height = min(self.height, len(self.options))
        self.scroll = len(self.options) - height
        self.cursor = height - 1

    def refresh(self, source):
        if self.timeout:
            now = time.time()
            if now > self.timeout:
                raise self.TimeoutSignal()
        raise self.RefreshSignal(source=source)

    def help(self):
        raise self.HelpSignal()

    def select(self, selection):
        raise self.SelectSignal(selection=selection)

    def _on_heartbeat(self):
        self.refresh("heartbeat")

    def _print_footer(self):
        if self.text is not None:
            filters = "".join(self.text).split(self.FILTER_SEPARATOR)
            mode = self.filter_mode
            mode_mark = ansi.colorize("\\", "yellow", bright=True) if mode.startswith("n") else ansi.colorize("/", "cyan", bright=True)
            if mode == "and":
                ansi.write("%s " % mode_mark)
            else:
                ansi.write(f"({mode}) {mode_mark} ")
            ansi.write(ansi.colorize(" , ", "white", bright=True).join(filters))
            ansi.show_cursor()

    def _print_menu(self):
        ansi.write("\r%s\n" % self.title)
        super()._print_menu()
        for _ in range(0, self.height - len(self.options)):
            ansi.clear_eol()
            ansi.write("\n")
        self._print_footer()

        ansi.clear_eol()

    def _goto_top(self):
        super()._goto_top()
        ansi.up(self.title_height)

    def get_total_height(self):
        return (self.title_height +  # header
                self.height          # options
                )

    def _clear_menu(self):
        super()._clear_menu()
        clear = getattr(self, "clear", True)
        ansi.restore_position()
        height = self.get_total_height()
        if clear:
            for i in range(height):
                ansi.clear_eol()
                ansi.up()
            ansi.clear_eol()
        else:
            ansi.up(height)
        ansi.clear_eol()
        ansi.write("\r")

    def _refilter(self):
        with self._selection_preserved():
            self._clear_cache()
            self.options = []
            texts = set(filter(None, "".join(self.text or []).lower().split(self.FILTER_SEPARATOR)))
            if self.filter_mode == "and":
                pred = lambda option: all(text in option.filter_text for text in texts)
            elif self.filter_mode == "nand":
                pred = lambda option: not all(text in option.filter_text for text in texts)
            elif self.filter_mode == "or":
                pred = lambda option: any(text in option.filter_text for text in texts)
            elif self.filter_mode == "nor":
                pred = lambda option: not any(text in option.filter_text for text in texts)
            else:
                assert False, self.filter_mode
            # filter the matching options
            for option in self._allOptions:
                if option.attrs.get("showAlways") or not texts or pred(option):
                    self.options.append(option)
            # select the first matching element (showAlways elements might not match)
            self.scroll = 0
            for i, option in enumerate(self.options):
                if not option.attrs.get("showAlways") and pred(option):
                    self.cursor = i
                    self.is_empty = False
                    break
            else:
                self.is_empty = True
                self.options.append(self._Option(" (No match for RED<<%s>>; WHITE@{<ESC>}@ to reset filter)" % " , ".join(map(repr,texts))))


def _get_option_name(sub):
    if hasattr(sub, "get_option_name"):
        return sub.get_option_name()
    return sub.__doc__ or sub.__name__


class AppMenu:

    class _MenuSignal(ParamsException): pass
    class RetrySignal(_MenuSignal): pass
    class AbortedSignal(KeyboardInterrupt, _MenuSignal): pass
    class QuitSignal(_MenuSignal): pass
    class BackSignal(_MenuSignal): pass
    class ReturnSignal(_MenuSignal): pass
    class TimeoutSignal(_MenuSignal): pass

    # yield this to add a separator
    SEPARATOR = dict(text="BLACK<<%s>>" % ("-"*80), result=True, selectable=False)

    _all_titles = []
    _all_menus = []

    @property
    def title(self):
        return self.__class__.__name__

    option_name = None
    @classmethod
    def get_option_name(cls):
        return cls.option_name or cls.__name__

    @property
    def height(self):
        return None  # use entire terminal height

    @property
    def items(self):

        # convert named submenus to submenu objects (functions/classes)
        submenus = (
            getattr(self, name) if isinstance(name, str) else name
            for name in self.submenus
        )

        return [
            sub if isinstance(sub, (dict, tuple)) else (_get_option_name(sub), sub)
            for sub in submenus
        ]

    submenus = []
    default = None
    multiselect = False
    fullscreen = True
    heartbeat = None
    width = None
    actions = None
    timeout = None

    def __init__(self, *args, **kwargs):
        parent = self._all_menus[-1] if self._all_menus else None
        self._all_menus.append(self)
        self.parent = parent
        self.return_value = None
        self.initialize(*args, **kwargs)
        try:
            self._menu_loop()
        finally:
            self._all_menus.pop(-1)

    def initialize(self, *args, **kwargs):
        pass

    def banner(self):
        pass

    def update_data(self):
        pass

    @contextmanager
    def showing(self):
        """
        Allow subclasses to run something before and after the menu is shown
        """
        yield

    def help(self):
        lines = [
            "WHITE@{Menu Usage:}@",
            "",
            " * Use the WHITE@{<Up/Down>}@ arrow keys to navigate the menu",
            " * Hit WHITE@{<ESC>}@ to return to the parent menu (or exit)",
            " * Hit WHITE@{<Ctrl-C>}@ to quit",
            " * Hit WHITE@{<F5>}@ to refresh/redraw",
            " * Hit WHITE@{<F1>}@ this help screen",
            " * Use any other key to filter the current selection (WHITE@{<ESC>}@ to clear the filter)",
            "", "",
        ]
        if self.multiselect:
            lines[3:3] = [
                " * Use WHITE@{`}@ or WHITE@{<Insert>}@ to select/deselect the currently active item",
                " * Use WHITE@{*}@ to toggle selection on all items",
                " * Hit WHITE@{<Enter>}@ to proceed with currently selected items, or with the active item if nothing is selected",
            ]
        else:
            lines[3:3] = [
                " * Hit WHITE@{<Enter>}@ to select",
            ]
        print(Colorized("\n".join(lines)))
        self.wait_for_keys(prompt="(Hit any key to continue)")

    def _menu_loop(self):

        # use the default only on the first iteration
        # after that we'll default to the the last selection
        menu = self.menu = TermenuAdapter(app=self)
        self.refresh = "first"
        selection = None
        default = self.default
        try:
            while True:
                if self.refresh:
                    self.update_data()

                    if self.fullscreen:
                        ansi.clear_screen()
                        ansi.home()
                    title = self.title
                    titles = [t() if isinstance(t, collections.abc.Callable) else t for t in self._all_titles + [title]]
                    banner = self.banner
                    if isinstance(banner, collections.abc.Callable):
                        banner = banner()
                    options = list(self.items)
                    if not options:
                        return self.result(None)

                    menu.reset(
                        title=" DARK_GRAY@{>>}@ ".join(titles),
                        header=banner,
                        options=options,
                        height=self.height,
                        multiselect=self.multiselect,
                        heartbeat=self.heartbeat or (1 if self.timeout else None),
                        width=self.width,
                        selection=selection,
                    )
                else:
                    # next time we must refresh
                    self.refresh = "second"

                with ExitStack() as stack:
                    self._all_titles.append(title)
                    stack.callback(lambda: self._all_titles.pop(-1))

                    try:
                        with self.showing():
                            selected = menu.show(default=default, auto_clear=not self.fullscreen)
                        default = None  # default selection only on first show
                    except KeyboardInterrupt:
                        self.quit()
                    except menu.RefreshSignal as e:
                        self.refresh = e.source
                        continue
                    except menu.HelpSignal:
                        self.help()
                        continue
                    except menu.TimeoutSignal:
                        raise self.TimeoutSignal("Timed out waiting for selection")
                    except menu.SelectSignal as e:
                        selected = e.selection

                    try:
                        self.on_selected(selected)
                    except self.RetrySignal as e:
                        self.refresh = e.refresh  # will refresh by default unless told differently
                        selection = e.selection
                        continue
                    except (KeyboardInterrupt):
                        self.refresh = False  # show the same menu
                        continue
                    except self.BackSignal as e:
                        if e.levels:
                            e.levels -= 1
                            raise
                        self.refresh = e.refresh
                        continue
                    else:
                        self.refresh = "second"   # refresh the menu

        except (self.QuitSignal, self.BackSignal):
            if self.parent:
                raise

        except self.ReturnSignal as e:
            self.return_value = e.value

        finally:
            if self.fullscreen:
                menu._clear_menu()

    def action(self, selected):
        def evaluate(item):
            if isinstance(item, type):
                # we don't want the instance of the class to be returned
                # as the a result from the menu. (See 'HitMe' class below)
                item, _ = None, item()
            if isinstance(item, collections.abc.Callable):
                item = item()
            if isinstance(item, self._MenuSignal):
                raise item
            if isinstance(item, AppMenu):
                return
            return item
        return list(map(evaluate, selected)) if self.multiselect else evaluate(selected)

    def on_selected(self, selected):
        if not selected and isinstance(selected, (NoneType, list)):
            self.back()

        actions = self.get_selection_actions(selected)

        if isinstance(actions, (list, tuple)):
            to_submenu = lambda action: (_get_option_name(action), functools.partial(action, selected))
            actions = [action if isinstance(action, collections.abc.Callable) else getattr(self, action) for action in actions]
            ret = self.show(title=self.get_selection_title(selected), options=list(map(to_submenu, actions)))
        else:
            if actions is None:
                action = self.action
            elif isinstance(actions, str):
                action = getattr(self, actions)
            else:
                action = actions
            ret = action(selected)

        if ret is not None:
            self.result(ret)

    def get_selection_actions(self, selection):
        # override this to change available actions per selection
        return self.actions

    def get_selection_title(self, selection):
        return "Selected %s items" % len(selection)

    @classmethod
    def retry(cls, refresh="app", selection=None):
        "Refresh into the current menu"
        raise cls.RetrySignal(refresh=refresh, selection=selection)

    @classmethod
    def back(cls, refresh=True, levels=1):
        "Go back to the parent menu"
        raise cls.BackSignal(refresh=refresh, levels=levels)

    @classmethod
    def result(cls, value):
        "Return result back to the parent menu"
        raise cls.ReturnSignal(value=value)

    @classmethod
    def quit(cls):
        "Quit the whole menu system"
        raise cls.QuitSignal()

    @staticmethod
    def show(title, options, default=None, back_on_abort=True, **kwargs):
        if callable(options):
            options = property(options)
        kwargs.update(title=title, items=options, default=default, back_on_abort=back_on_abort)
        menu = type("AdHocMenu", (AppMenu,), kwargs)()
        return menu.return_value

    @staticmethod
    def wait_for_keys(keys=("enter", "esc"), prompt=None):
        if prompt:
            ansi.write(Colorized(prompt))  # Aviod bocking
            ansi.write(" ")
            ansi.show_cursor()

        keys = set(keys)
        try:
            for key in keyboard.keyboard_listener():
                if not keys or key in keys:
                    print()
                    return key
        finally:
            ansi.hide_cursor()

    def terminal_released(self):
        return termenu.Termenu.terminal.closed()
