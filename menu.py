import time
import functools
import termenu
from contextlib import contextmanager
from . import ansi
from colors import Colorized

#===============================================================================
# Termenu
#===============================================================================


class TermenuAdapter(termenu.Termenu):

    class RefreshSignal(Exception):  pass
    class TimeoutSignal(Exception):  pass

    FILTER_SEPARATOR = ","
    EMPTY = "DARK_RED<< (Empty) >>"

    class _Option(termenu.Termenu._Option):
        def __init__(self, *args, **kwargs):
            super(TermenuAdapter._Option, self).__init__(*args, **kwargs)
            self.raw = self.text
            self.text = Colorized(self.raw)
            if isinstance(self.result, str):
                self.result = ansi.decolorize(self.result)

    def __init__(self, timeout=None):
        self.text = None
        self.is_empty = True
        self.dirty = False
        self.timeout = (time.time() + timeout) if timeout else None

    def reset(self, title="No Title", header="", *args, **kwargs):
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
        self.title = Colorized(title)
        self.title_height = len(title.splitlines())
        with self._selection_preserved():
            super(TermenuAdapter, self).__init__(*args, **kwargs)

    def _make_option_objects(self, options):
        options = super(TermenuAdapter, self)._make_option_objects(options)
        self._allOptions = options[:]
        return options

    def _decorate(self, option, **flags):
        "Decorate the option to be displayed"

        active = flags.get("active", False)
        selected = flags.get("selected", False)
        moreAbove = flags.get("moreAbove", False)
        moreBelow = flags.get("moreBelow", False)

        # add selection / cursor decorations
        option = Colorized(("WHITE<<*>> " if selected else "  ") + ("WHITE@{>}@" if active else " ") + option)
        option = str(option)  # convert from Colorized to ansi string
        if active:
            option = ansi.highlight(option, "black")

        # add more above/below indicators
        if moreAbove:
            option = option + " " + ansi.colorize("^", "white", bright=True)
        elif moreBelow:
            option = option + " " + ansi.colorize("v", "white", bright=True)
        else:
            option = option + "  "

        return option

    @contextmanager
    def _selection_preserved(self):
        if self.is_empty:
            yield
            return

        prev_active = self._get_active_option().result
        prev_selected = set(o.result for o in self.options if o.selected)
        try:
            yield
        finally:
            if prev_selected:
                for option in self.options:
                    option.selected = option.result in prev_selected
            self._set_default(prev_active)

    def show(self, default=None):
        self._refilter()
        self._clear_cache()
        self._set_default(default)
        return super(TermenuAdapter, self).show()

    def _set_default(self, default):
        if default is None:
            return
        for index, o in enumerate(self.options):
            if o.result == default:
                break
        else:
            return
        for i in xrange(index):
            self._on_down()

    def _adjust_width(self, option):
        option = Colorized("BLACK<<\\>>").join(option.splitlines())
        l = len(option)
        w = max(self.width, 8)
        if l > w:
            option = termenu.shorten(option, w)
        if l < w:
            option += " " * (w - l)
        return option

    def _on_key(self, key):
        prevent = False
        if not key == "heartbeat":
            self.timeout = None

        if key == "*" and self.multiselect:
            for option in self.options:
                if not option.attrs.get("header"):
                    option.selected = not option.selected
        elif len(key) == 1 and 32 < ord(key) <= 127:
            if not self.text:
                self.text = []
            self.text.append(key)
            self._refilter()
        elif self.is_empty and key == "enter":
            prevent = True
        elif self.text and key == "backspace":
            del self.text[-1]
            self._refilter()
        elif self.text is not None and key == "esc":
            filters = "".join(self.text or []).split(self.FILTER_SEPARATOR)
            if filters:
                filters.pop(-1)
            self.text = list(self.FILTER_SEPARATOR.join(filters)) if filters else None
            termenu.ansi.hide_cursor()
            prevent = True
            self._refilter()
        elif key == "end":
            self._on_end()
            prevent = True
        elif key == "F5":
            self.refresh()

        if not prevent:
            return super(TermenuAdapter, self)._on_key(key)

    def _on_end(self):
        height = min(self.height, len(self.options))
        self.scroll = len(self.options) - height
        self.cursor = height - 1

    def refresh(self):
        if self.timeout:
            now = time.time()
            if now > self.timeout:
                raise self.TimeoutSignal()
        raise self.RefreshSignal()

    def _on_heartbeat(self):
        self.refresh()

    def _print_footer(self):
        if self.text is not None:
            filters = "".join(self.text).split(self.FILTER_SEPARATOR)
            termenu.ansi.write("/%s" % termenu.ansi.colorize(" , ", "white", bright=True).join(filters))
            termenu.ansi.show_cursor()

    def _print_menu(self):
        ansi.write("\r%s\n" % self.title)
        super(TermenuAdapter, self)._print_menu()
        for _ in xrange(0, self.height - len(self.options)):
            termenu.ansi.clear_eol()
            termenu.ansi.write("\n")
        self._print_footer()

        termenu.ansi.clear_eol()

    def _goto_top(self):
        super(TermenuAdapter, self)._goto_top()
        ansi.up(self.title_height)

    def get_total_height(self):
        return (self.title_height +  # header
                self.height          # options
                )

    def _clear_menu(self):
        super(TermenuAdapter, self)._clear_menu()
        clear = getattr(self, "clear", True)
        termenu.ansi.restore_position()
        height = self.get_total_height()
        if clear:
            for i in xrange(height):
                termenu.ansi.clear_eol()
                termenu.ansi.up()
            termenu.ansi.clear_eol()
        else:
            termenu.ansi.up(height)
        ansi.clear_eol()
        ansi.write("\r")

    def _refilter(self):
        with self._selection_preserved():
            self._clear_cache()
            self.options = []
            texts = "".join(self.text or []).lower().split(self.FILTER_SEPARATOR)
            pred = lambda option: all(text in option.text.lower() for text in texts)
            # filter the matching options
            for option in self._allOptions:
                if option.attrs.get("showAlways") or pred(option):
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
                self.options.append(self._Option(" (No match for RED<<%s>>)" % " , ".join(map(repr,texts))))

class ParamsException(Exception):
    "An exception object that accepts arbitrary params as attributes"
    def __init__(self, message="", *args, **kwargs):
        if args:
            message %= args
        self.message = message
        for k, v in kwargs.iteritems():
            setattr(self, k, v)
        self.params = kwargs


class AppMenu(object):

    class _MenuSignal(ParamsException): pass
    class RetrySignal(_MenuSignal): pass
    class AbortedSignal(KeyboardInterrupt, _MenuSignal): pass
    class QuitSignal(_MenuSignal): pass
    class BackSignal(_MenuSignal): pass
    class ReturnSignal(_MenuSignal): pass
    class TimeoutSignal(_MenuSignal): pass

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
        return termenu.get_terminal_size()[1] / 2

    @property
    def items(self):

        get_option_name = lambda sub: (
            sub.get_option_name()
            if hasattr(sub, "get_option_name")
            else (sub.__doc__ or sub.__name__)
        )

        # convert named submenus to submenu objects (functions/classes)
        submenus = (
            getattr(self, name) if isinstance(name, basestring) else name
            for name in self.submenus
        )

        return [
            sub if isinstance(sub, tuple) else (get_option_name(sub), sub)
            for sub in submenus
        ]

    submenus = []
    default = None
    multiselect = False
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

    def _menu_loop(self):

        # use the default only on the first iteration
        # after that we'll default to the the last selection
        menu = TermenuAdapter(timeout=self.timeout)
        refresh = True
        default = self.default

        try:
            while True:
                if refresh:
                    title = self.title
                    titles = [t() if callable(t) else t for t in self._all_titles + [title]]
                    banner = self.banner
                    if callable(banner):
                        banner = banner()

                    menu.reset(
                        title=" DARK_GRAY@{>>}@ ".join(titles),
                        header=banner,
                        options=self.items,
                        height=self.height,
                        multiselect=self.multiselect,
                        heartbeat=self.heartbeat or (1 if self.timeout else None),
                        width=self.width,
                    )
                else:
                    # next time we must refresh
                    refresh = True

                try:
                    selected = menu.show(default=default)
                    default = None  # default selection only on first show
                except KeyboardInterrupt:
                    self.quit()
                except menu.RefreshSignal:
                    continue
                except menu.TimeoutSignal:
                    raise self.TimeoutSignal("Timed out waiting for selection")

                self._all_titles.append(title)
                try:
                    self.on_selected(selected)
                except self.RetrySignal, e:
                    refresh = e.refresh  # will refresh by default unless told differently
                    continue
                except (KeyboardInterrupt):
                    refresh = False  # show the same menu
                    continue
                except self.BackSignal, e:
                    if e.levels:
                        e.levels -= 1
                        raise
                    refresh = e.refresh
                    continue
                else:
                    refresh = True   # refresh the menu
                finally:
                    self._all_titles.pop(-1)

        except (self.QuitSignal, self.BackSignal):
            if self.parent:
                raise

        except self.ReturnSignal, e:
            self.return_value = e.value

    def action(self, selected):
        def evaluate(item):
            if isinstance(item, type):
                # we don't want the instance of the class to be returned
                # as the a result from the menu. (See 'HitMe' class below)
                item, _ = None, item()
            if callable(item):
                item = item()
            if isinstance(item, self._MenuSignal):
                raise item
            if isinstance(item, AppMenu):
                return
            return item
        return map(evaluate, selected) if hasattr(selected, "__iter__") else evaluate(selected)

    def on_selected(self, selected):
        if not selected:
            self.back()

        actions = self.get_selection_actions(selected)

        if actions is None:
            ret = self.action(selected)
        else:
            to_submenu = lambda action: (action.__doc__ or action.__name__, functools.partial(action, selected))
            actions = [action if callable(action) else getattr(self, action) for action in actions]
            ret = self.show_menu(title=self.get_selection_title(selected), options=map(to_submenu, actions))

        if ret is not None:
            self.result(ret)

    def get_selection_actions(self, selection):
        # override this to change available actions per selection
        return self.actions

    def get_selection_title(self, selection):
        return "Selected %s items" % len(selection)

    @classmethod
    def retry(cls, refresh=True):
        "Refresh into the current menu"
        raise cls.RetrySignal(refresh=refresh)

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
    def show_menu(title, options, default=None, back_on_abort=True, **kwargs):
        kwargs.update(title=title, items=options, default=default, back_on_abort=back_on_abort)
        menu = type("AdHocMenu", (AppMenu,), kwargs)()
        return menu.return_value




def test1():

    class TopMenu(AppMenu):
        title = staticmethod(lambda: "YELLOW<<%s>>" % time.ctime())
        timeout = 15
        submenus = ["Letters", "Numbers", "Submenu", "Foo", "Bar"]

        class Letters(AppMenu):
            title = "CYAN(BLUE)<<Letters>>"
            option_name = "BLUE<<Letters>>"
            multiselect = True
            items = [chr(i) for i in xrange(65, 91)]
            def action(self, letters):
                raw_input("Selected: %s" % "".join(letters))

        class Numbers(AppMenu):
            multiselect = True
            @property
            def items(self):
                return range(int(time.time()*2) % 10, 50)
            def get_selection_actions(self, selection):
                yield "MinMax"
                yield "Add"
                if min(selection) > 0:
                    yield "Multiply"
                yield "Quit"
            def get_selection_title(self, selection):
                return ", ".join(map(str, sorted(selection)))
            def MinMax(self, numbers):
                "Min/Max"
                raw_input("Min: %s, Max: %s" % (min(numbers), max(numbers)))
                self.retry()
            def Add(self, numbers):
                raw_input("Sum: %s" % sum(numbers))
                self.back()
            def Multiply(self, numbers):
                raw_input("Mult: %s" % reduce((lambda a, b: a*b), numbers))
            def Quit(self, numbers):
                raw_input("%s" % numbers)
                self.quit()


        class Submenu(AppMenu):
            submenus = ["Letter", "Number"]

            class Letter(AppMenu):
                @property
                def items(self):
                    return [chr(i) for i in xrange(65, 91)][int(time.time()*2) % 10:][:10]
                def action(self, letter):
                    raw_input("Selected: %s" % letter)
                    self.back()

            class Number(AppMenu):
                items = range(50)
                def action(self, number):
                    raw_input("Sum: %s" % number)

        def Foo(self):
            raw_input("Foo?")

        def Bar(object):
            raw_input("Bar! ")
        Bar.get_option_name = lambda: "Dynamic option name: (%s)" % (int(time.time()) % 20)

    TopMenu()


def test2():

    def leave():
        print "Leave..."
        AppMenu.quit()

    def go():
        def back():
            print "Going back."
            AppMenu.back()

        def there():
            ret = AppMenu.show_menu("Where's there?",
                "Spain France Albania".split() + [("Quit", AppMenu.quit)],
                multiselect=True, back_on_abort=True)
            print ret
            return ret

        return AppMenu.show_menu("Go Where?", [
            ("YELLOW<<Back>>", back),
            ("GREEN<<There>>", there)
        ])

    return AppMenu.show_menu("Make your MAGENTA<<decision>>", [
        ("RED<<Leave>>", leave),
        ("BLUE<<Go>>", go)
    ])


if __name__ == '__main__':
    import pdb
    try:
        ret = AppMenu.show_menu("AppMenu", [
            ("Debug", pdb.set_trace),
            ("Test1", test1),
            ("Test2", test2)
            ],
            timeout=5, heartbeat=1,
        )
        print "Result is:", ret
    except AppMenu.TimeoutSignal:
        print "Timed out"