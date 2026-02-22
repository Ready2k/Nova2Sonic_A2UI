class A:
    def call_it(self):
        return self.foo()
    def foo(self):
        return "original"

a = A()
def patched():
    return "patched"
a.foo = patched
print(a.call_it())
