class BaseInput:
    def __init__(self, name=""):
        self.name = name

    def open(self):
        pass

    def close(self):
        pass

    def read(self):
        pass
