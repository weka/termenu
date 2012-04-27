import sys
sys.path.append("..")

import ansi

def pluggable(method):
    def _wrapped(self, *args, **kw):
        post = []
        prevent = False
        # run plugin pre-processing
        for plugin in self.plugins:
            gen = getattr(plugin, method.__name__)(*args, **kw)
            prevent = gen.next()
            if prevent:
                break
            post.append(gen)
        # run default implementation
        result = None
        if not prevent:
            result = method(self, *args, **kw)
        # run plugin post-processing in reverse order
        for gen in reversed(post):
            try:
                gen.next()
            except StopIteration:
                pass
        return result
    return _wrapped

class Termenu(object):
    class Option(object):
        def __init__(self, text, result):
            self.text = text
            self.result = result
            self.selected = False
        def __str__(self):
            return self.text
        def __len__(self):
            return len(self.text)

    def __init__(self, options, results=None, default=None, height=None, multiselect=True, plugins=None):
        self.options = [self.Option(o, r) for o, r in zip(options, results or options)]
        self.height = min(height or 10, len(options))
        self.multiselect = multiselect
        self.plugins = []
        self.cursor = 0
        self.scroll = 0
        self._maxOptionLen = max(len(o) for o in self.options)
        self._aborted = False
        self._set_default(default)
        self._set_plugins(plugins or [])

    def get_result(self):
        if self._aborted:
            return None
        else:
            selected = [o.result for o in self.options if o.selected]
            if not selected:
                selected.append(self._get_active_option().result)
            return selected

    def show(self):
        import keyboard
        self._print_menu()
        ansi.save_position()
        ansi.hide_cursor()
        try:
            for key in keyboard.keyboard_listener():
                stop = self._on_key(key)
                if stop:
                    return self.get_result()
                ansi.restore_position()
                ansi.up(self.height)
                self._print_menu()
        finally:
            self._clear_menu()
            ansi.show_cursor()

    def _set_default(self, default):
        # handle default selection of multiple options
        if isinstance(default, list) and default:
            if not self.multiselect:
                raise ValueError("multiple defaults passed, but multiselect is False")
            for option in self.options:
                if str(option) in default:
                    option.selected = True
            default = default[0]

        # handle default active option
        index = self._get_index(default)
        if index is not None:
            if index < self.height:
                self.cursor = index % self.height
                self.scroll = 0
            elif index + self.height < len(self.options) - 1:
                self.cursor = 0
                self.scroll = index
            else:
                self.cursor = index % self.height + 1
                self.scroll = len(self.options) - self.height

    def _set_plugins(self, plugins):
        self.plugins = []
        for plugin in plugins:
            plugin.menu = self
            self.plugins.append(plugin)

    def _get_index(self, s):
        matches = [i for i, o in enumerate(self.options) if str(o) == s]
        if matches:
            return matches[0]
        else:
            return None

    def _get_active_option(self):
        return self.options[self.scroll+self.cursor] if self.options else None

    def _get_window(self):
        return self.options[self.scroll:self.scroll+self.height]

    def _get_debug_view(self):
        options = []
        for i, option in enumerate(self._get_window()):
            options.append(("(%s)" if i == self.cursor else "%s") % option)
        return " ".join(options)

    @pluggable
    def _on_key(self, key):
        func = "_on_" + key
        if hasattr(self, func):
            return getattr(self, func)()

    def _on_down(self):
        height = min(self.height, len(self.options))
        if self.cursor < height - 1:
            self.cursor += 1
        elif self.scroll + height < len(self.options):
            self.scroll += 1

    def _on_up(self):
        if self.cursor > 0:
            self.cursor -= 1
        elif self.scroll > 0:
            self.scroll -= 1

    def _on_pageDown(self):
        height = min(self.height, len(self.options))
        if self.cursor < height - 1:
            self.cursor = height - 1
        elif self.scroll + height * 2 < len(self.options):
            self.scroll += height
        else:
            self.scroll = len(self.options) - height

    def _on_pageUp(self):
        height = min(self.height, len(self.options))
        if self.cursor > 0:
            self.cursor = 0
        elif self.scroll - height >= 0:
            self.scroll -= height
        else:
            self.scroll = 0

    def _on_space(self):
        if not self.multiselect:
            return
        option = self._get_active_option()
        option.selected = not option.selected
        self._on_down()

    def _on_esc(self):
        self._aborted = True
        return True # stop loop

    def _on_enter(self):
        return True # stop loop

    def _clear_menu(self):
        ansi.restore_position()
        for i in xrange(self.height):
            ansi.clear_eol()
            ansi.up()
        ansi.clear_eol()

    @pluggable
    def _print_menu(self):
        _write("\r")
        for i, option in enumerate(self._get_window()):
            _write(self._decorate(option, **self._decorate_flags(i)) + "\n")

    def _decorate_flags(self, i):
        return dict(
            active = (self.cursor == i),
            selected = (self.options[self.scroll+i].selected),
            moreAbove = (self.scroll > 0 and i == 0),
            moreBelow = (self.scroll + self.height < len(self.options) and i == self.height - 1),
        )

    def _decorate(self, option, active=False, selected=False, moreAbove=False, moreBelow=False):
        # all height to same width
        option = "{0:<{width}}".format(option, width=self._maxOptionLen)

        # add selection / cursor decorations
        if active and selected:
            option = "*" + ansi.colorize(option, "red", "white")
        elif active:
            option = " " + ansi.colorize(option, "black", "white")
        elif selected:
            option = "*" + ansi.colorize(option, "red")
        else:
            option = " " + option

        # add more above/below indicators
        if moreAbove:
            option = option + " " + ansi.colorize("^", "white", bright=True)
        elif moreBelow:
            option = option + " " + ansi.colorize("v", "white", bright=True)
        else:
            option = option + "  "

        return option

class Plugin(object):
    # has access to self.menu
    def _on_key(self, key):
        # put preprocessing here
        yield # yield True will prevent other plugins from processing this function
        # put postprocessing here

    def _print_menu(self, key):
        yield

class FilterPlugin(Plugin):
    def __init__(self):
        self.text = None

    def _on_key(self, key):
        prevent = False
        if len(key) == 1 and 32 < ord(key) <= 127:
            if not self.text:
                self.text = []
            self.text.append(key)
            self._refilter()
        elif self.text and key == "backspace":
            del self.text[-1]
            self._refilter()
        elif self.text is not None and key == "esc":
            self.text = None
            ansi.hide_cursor()
            prevent = True
            self._refilter()

        yield prevent

    def _print_menu(self):
        yield

        for i in xrange(0, self.menu.height - len(self.menu.options)):
            ansi.clear_eol()
            _write("\n")
        if self.text is not None:
            _write("/" + "".join(self.text))
            ansi.show_cursor()
        ansi.clear_eol()

    def _refilter(self):
        self.menu.options = []
        text = "".join(self.text or []).lower()
        for option in self._allOptions:
            if text in str(option).lower():
                self.menu.options.append(option)
        #FIXME: it would be better to keep the selection
        self.menu.cursor = 0
        self.menu.scroll = 0

    #FIXME: perhaps an attach() plugin method called by Termenu would be cleaner
    def _set_menu(self, menu):
        self._menu = menu
        self._allOptions = menu.options[:]
    def _get_menu(self):
        return self._menu
    menu = property(_get_menu, _set_menu)

class Minimenu(object):
    def __init__(self, options, default=None):
        self.options = options
        try:
            self.cursor = options.index(default)
        except ValueError:
            self.cursor = 0

    def show(self):
        import keyboard
        ansi.hide_cursor()
        self._print_menu(rewind=False)
        try:
            for key in keyboard.keyboard_listener():
                if key == "enter":
                    self._clear_menu()
                    _write(self.options[self.cursor])
                    return self.options[self.cursor]
                elif key == "esc":
                    self._clear_menu()
                    _write("<esc>")
                    return None
                elif key == "left":
                    self.cursor = max(0, self.cursor - 1)
                elif key == "right":
                    self.cursor = min(len(self.options) - 1, self.cursor + 1)
                self._print_menu(rewind=True)
        finally:
            ansi.show_cursor()
            _write("\n")

    def _make_menu(self, _decorate=True):
        menu = []
        for i, option in enumerate(self.options):
            if _decorate:
                menu.append(ansi.colorize(option, "black", "white") if i == self.cursor else option)
            else:
                menu.append(option)
        menu = " ".join(menu)
        return menu

    def _print_menu(self, rewind):
        menu = self._make_menu()
        if rewind:
            menu = "\b"*len(self._make_menu(decorate=False)) + menu
        _write(menu)

    def _clear_menu(self):
        menu = self._make_menu(decorate=False)
        _write("\b"*len(menu)+" "*len(menu)+"\b"*len(menu))

def _write(s):
    sys.stdout.write(s)
    sys.stdout.flush()

def redirect_std():
    """
    Connect stdin/stdout to controlling terminal even if the scripts input and output
    were redirected. This is useful in utilities based on termenu.
    """
    stdin = sys.stdin
    stdout = sys.stdout
    if not sys.stdin.isatty():
        sys.stdin = open("/dev/tty", "r", 0)
    if not sys.stdout.isatty():
        sys.stdout = open("/dev/tty", "w", 0)
    return stdin, stdout

if __name__ == "__main__":
    menu = Termenu(["option-%06d" % i for i in xrange(1,100)], height=10, plugins=[FilterPlugin()])
    print menu.show()
#~     print "Would you like to continue? ",
#~     result = Minimenu(["Abort", "Retry", "Fail"], "Fail").show()
#~     print result