"""Small Tk presentation helpers. No model, compiler or transport behavior."""
import tkinter as tk
from tkinter import ttk

CHOICES={
    'linear':'直线 / 匀速','bezier':'贝塞尔曲线','smooth':'自动平滑',
    'ease_in':'缓入','ease_out':'缓出','ease_in_out':'缓入缓出',
    'mouse':'鼠标按键','keyboard':'本机键盘',
}


class MappedCombobox(ttk.Combobox):
    """Friendly displayed labels, original enum kept in the supplied StringVar."""
    def __init__(self,parent,*,textvariable,values,**kwargs):
        self.value_var=textvariable;self.labels={v:CHOICES.get(v,v) for v in values}
        self.display_var=tk.StringVar(master=parent,value=self.labels.get(textvariable.get(),textvariable.get()))
        self._syncing=False
        super().__init__(parent,textvariable=self.display_var,values=list(self.labels.values()),**kwargs)
        self._trace=textvariable.trace_add('write',self._from_model)
        self.bind('<<ComboboxSelected>>',self._to_model,add='+')
        self.bind('<Destroy>',self._cleanup,add='+')

    def _from_model(self,*_):
        self.display_var.set(self.labels.get(self.value_var.get(),self.value_var.get()))

    def _to_model(self,event=None):
        value=next((v for v,label in self.labels.items() if label==self.display_var.get()),self.display_var.get())
        self.value_var.set(value)

    def _cleanup(self,event):
        if event.widget is self:self.value_var.trace_remove('write',self._trace)


class Tooltip:
    def __init__(self,widget,text):
        self.widget=widget;self.text=text;self.timer=None;self.window=None
        widget.bind('<Enter>',self.schedule,add='+')
        for seq in ('<Leave>','<ButtonPress>','<Destroy>'):widget.bind(seq,self.hide,add='+')

    def schedule(self,event=None):
        self.hide();self.timer=self.widget.after(550,self.show)

    def show(self):
        self.timer=None
        if not self.widget.winfo_exists():return
        self.window=tk.Toplevel(self.widget);self.window.overrideredirect(True)
        self.window.geometry(f'+{self.widget.winfo_rootx()+8}+{self.widget.winfo_rooty()+self.widget.winfo_height()+6}')
        tk.Label(self.window,text=self.text,justify='left',wraplength=380,bg='#edf3fa',fg='#172b43',
                 font=('Microsoft YaHei UI',10),padx=10,pady=7).pack()

    def hide(self,event=None):
        if self.timer:
            self.widget.after_cancel(self.timer);self.timer=None
        if self.window:self.window.destroy();self.window=None


class Disclosure(ttk.Frame):
    def __init__(self,parent,title,*,expanded=False):
        super().__init__(parent)
        self.title=title;self.expanded=expanded
        self.header=ttk.Button(self,command=self.toggle,style='Disclosure.TButton')
        self.header.pack(fill='x')
        self.body=ttk.Frame(self,padding=(4,8,4,4))
        self._layout()

    def _layout(self):
        self.header.configure(text=('▾  ' if self.expanded else '▸  ')+self.title)
        if self.expanded:self.body.pack(fill='x')
        else:self.body.pack_forget()

    def toggle(self):self.expanded=not self.expanded;self._layout()


class ScrollFrame(ttk.Frame):
    """Scroll only this panel; never hijack wheel input over the XY canvas."""
    def __init__(self,parent,**kwargs):
        super().__init__(parent,**kwargs)
        if 'width' in kwargs:self.grid_propagate(False)
        self.columnconfigure(0,weight=1);self.rowconfigure(0,weight=1)
        self.canvas=tk.Canvas(self,bg='#1b2839',highlightthickness=0,width=1,height=1)
        self.canvas.grid(row=0,column=0,sticky='nsew')
        self.scrollbar=ttk.Scrollbar(self,orient='vertical',command=self.canvas.yview)
        self.scrollbar.grid(row=0,column=1,sticky='ns');self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.body=ttk.Frame(self.canvas,padding=12)
        self.item=self.canvas.create_window(0,0,window=self.body,anchor='nw')
        self._bound=set();self.body.bind('<Configure>',self._content)
        self.canvas.bind('<Configure>',lambda e:self.canvas.itemconfigure(self.item,width=e.width))
        self.canvas.bind('<MouseWheel>',self.wheel)

    def _content(self,event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox('all'))
        def bind(widget):
            if widget not in self._bound:
                self._bound.add(widget)
                if not isinstance(widget,(tk.Text,tk.Listbox,ttk.Treeview)):
                    widget.bind('<MouseWheel>',self.wheel,add='+')
            for child in widget.winfo_children():bind(child)
        bind(self.body)

    def wheel(self,event):
        self.canvas.yview_scroll(-int(event.delta/120) or (-1 if event.delta>0 else 1),'units')
        return 'break'

    def reveal(self,widget):
        self.update_idletasks()
        total=max(1,self.body.winfo_height())
        self.canvas.yview_moveto(max(0,(widget.winfo_rooty()-self.body.winfo_rooty()-16)/total))


class DockablePanel(ttk.Frame):
    """Existing panel controller can live in a workspace or its original dialog."""
    def __init__(self,app,parent=None,*,title='',geometry='1000x760',minsize=(760,600)):
        self.app=app;self._title=title
        self.window=tk.Toplevel(app.root) if parent is None else None
        super().__init__(self.window or parent)
        self.pack(fill='both',expand=True)
        if self.window:
            self.window.title(title);self.window.geometry(geometry);self.window.minsize(*minsize)
            self.window.protocol('WM_DELETE_WINDOW',self.destroy)

    def title(self,value=None):
        if value is not None:
            self._title=value
            if self.window:self.window.title(value)
        return self._title

    def geometry(self,value=None):return (self.window or self.app.root).geometry(value)
    def minsize(self,*args):return (self.window or self.app.root).minsize(*args)
    def protocol(self,*args):
        if self.window:return self.window.protocol(*args)
    def lift(self,*args):
        if self.window:return self.window.lift(*args)
        return super().lift(*args)
    def destroy(self):
        window=self.window;self.window=None
        super().destroy()
        if window and window.winfo_exists():window.destroy()
