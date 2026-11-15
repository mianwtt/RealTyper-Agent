"""A task-local editor view anchored at the user's insertion point."""


class ScopeError(RuntimeError):
    pass


class InsertionScope:
    def __init__(self,editor):
        self.editor=editor
        editor.guard()
        original=editor.read()
        start,end=editor.selection()
        if not 0<=start<=end<=len(original):
            raise ScopeError("无法确认当前光标位置，未开始输入。")
        self.collapsed_selection=start!=end
        if self.collapsed_selection:
            # Preserve all selected user text; never replace it as a side effect.
            editor.select(end,end,expected=original)
            if editor.selection()!=(end,end):
                raise ScopeError("无法收起选区并确认插入位置，未开始输入。")
            start=end
        self.start=start
        self.prefix,self.suffix=original[:start],original[start:]
        self.ensure_pristine()

    def __getattr__(self,name):
        return getattr(self.editor,name)

    def snapshot(self):
        full=self.editor.read()
        if (len(full)<len(self.prefix)+len(self.suffix)
                or not full.startswith(self.prefix) or not full.endswith(self.suffix)):
            raise ScopeError("本次输入范围外的文字发生变化，已停止进一步操作。")
        end=len(full)-len(self.suffix)
        return full,full[self.start:end]

    def guard(self):
        self.editor.guard()
        self.snapshot()

    def read(self):
        return self.snapshot()[1]

    def selection(self):
        _,inserted=self.snapshot()
        start,end=self.editor.selection()
        if not self.start<=start<=end<=self.start+len(inserted):
            raise ScopeError("光标已离开本次输入范围，已停止。")
        return start-self.start,end-self.start

    def select(self,start,end,expected):
        self.editor.guard()
        full,inserted=self.snapshot()
        if inserted!=expected:
            raise ScopeError("本次输入内容发生变化，已取消定位修正。")
        if not 0<=start<=end<=len(inserted):
            raise ScopeError("修正位置超出本次输入范围，已停止。")
        self.editor.select(self.start+start,self.start+end,expected=full)

    def ensure_pristine(self):
        self.guard()
        if self.read() or self.selection()!=(0,0):
            raise ScopeError("规划期间文字或光标位置发生变化，未开始输入。")
