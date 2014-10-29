import time
from termenu.app import AppMenu

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


if __name__ == "__main__":
    TopMenu()
