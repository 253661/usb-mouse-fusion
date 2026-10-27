"""Tk canvas: lightweight world curves during gestures, compile after release."""
import math
import tkinter as tk
from PIL import Image, ImageTk
from .compiler import curve_position, segment_handles
from .project_io import decode_image

BG = '#111a27'
GRID = '#233246'
TEXT = '#bac9d9'
BLUE = '#67b5ff'
CYAN = '#62e4ca'
ORANGE = '#f8b563'


class TrajectoryCanvas(tk.Canvas):
    def __init__(self, master, app):
        super().__init__(master,background=BG,highlightthickness=0,takefocus=True)
        self.app = app
        self.gesture = None
        self.handles = []
        self._redraw_id = None
        self._source_key = self._source = self._render_key = self._photo = None
        self.bind('<Configure>',lambda e:self.request_redraw())
        self.bind('<ButtonPress-1>',self.press)
        self.bind('<B1-Motion>',self.motion)
        self.bind('<ButtonRelease-1>',self.release)
        self.bind('<Double-Button-1>',self.double_click)
        self.bind('<ButtonPress-2>',self.pan_start)
        self.bind('<B2-Motion>',self.pan_move)
        self.bind('<ButtonRelease-2>',self.pan_end)
        self.bind('<ButtonPress-3>',self.pan_start)
        self.bind('<B3-Motion>',self.pan_move)
        self.bind('<ButtonRelease-3>',self.pan_end)
        self.bind('<MouseWheel>',self.wheel)
        self.bind('<Motion>',self.hover)
        self.bind('<Escape>',lambda e:self.cancel_gesture())

    @property
    def editor(self): return self.app.editor
    @property
    def view(self): return self.editor.project.view
    def to_screen(self,x,y): return self.view.world_to_screen(x,y,self.winfo_width(),self.winfo_height())
    def to_world(self,x,y): return self.view.screen_to_world(x,y,self.winfo_width(),self.winfo_height())

    def request_redraw(self):
        if self._redraw_id is None: self._redraw_id = self.after(16,self.redraw)

    def _image(self,w,h):
        image = self.editor.mode.image
        if not image or not image.visible or not image.data: return
        if self._source_key != image.data:
            self._source = decode_image(image); self._source_key = image.data; self._render_key = None
        key = (image.x,image.y,image.scale,image.rotation,image.opacity,
               self.view.center_x,self.view.center_y,self.view.zoom,w,h)
        if self._render_key != key:
            # Affine inverse maps screen pixels to image pixels. Output is viewport-sized
            # even for very large view zoom, so no giant intermediate image is allocated.
            c,s = math.cos(math.radians(image.rotation)),math.sin(math.radians(image.rotation))
            zoom = self.view.zoom; scale = image.scale
            x0,y0 = self.to_world(0,0)
            affine = (c/(zoom*scale),-s/(zoom*scale),(c*(x0-image.x)+s*(y0-image.y))/scale,
                      s/(zoom*scale),c/(zoom*scale),(s*(x0-image.x)-c*(y0-image.y))/scale)
            rendered = self._source.transform((w,h),Image.Transform.AFFINE,affine,
                                              resample=Image.Resampling.BILINEAR)
            alpha = rendered.getchannel('A').point(lambda a:round(a*image.opacity))
            rendered.putalpha(alpha)
            self._photo = ImageTk.PhotoImage(rendered,master=self)
            self._render_key = key
        self.create_image(0,0,image=self._photo,anchor='nw')
        if self.app.edit_mode.get() == '图片编辑':
            corners = [self.to_screen(*image.pixel_to_world(x,y)) for x,y in
                       ((0,0),(image.width,0),(image.width,image.height),(0,image.height))]
            self.create_polygon(*[v for p in corners for v in p],fill='',outline=ORANGE,
                                dash=(5,4),width=1)
            rx,ry = self.to_screen(*image.pixel_to_world(image.reference_x,image.reference_y))
            self.create_line(rx-6,ry,rx+6,ry,fill=ORANGE)
            self.create_line(rx,ry-6,rx,ry+6,fill=ORANGE)
            self.create_text(14,18,text='底图已锁定' if image.locked else '底图：拖动移动 · Alt+滚轮缩放',
                             anchor='w',fill=ORANGE,font=('Microsoft YaHei UI',10))

    def redraw(self):
        if self._redraw_id:
            self.after_cancel(self._redraw_id); self._redraw_id = None
        if not self.winfo_exists(): return
        w,h = max(1,self.winfo_width()),max(1,self.winfo_height())
        self.delete('all'); self.handles.clear()
        self._image(w,h)
        spacing = self.app.grid_spacing()
        if self.app.flags['grid'].get():
            draw_step = spacing
            while draw_step*self.view.zoom < 24: draw_step *= 2
            while draw_step*self.view.zoom > 110: draw_step /= 2
            left,top = self.to_world(0,0); right,bottom = self.to_world(w,h)
            x = math.ceil(left/draw_step)*draw_step
            for _ in range(200):
                if x > right: break
                sx,_ = self.to_screen(x,0)
                self.create_line(sx,0,sx,h,fill=GRID)
                self.create_text(sx+3,h-38,text=f'{x:g}',fill='#8499ad',anchor='w',font=('Consolas',8))
                x += draw_step
            y = math.ceil(bottom/draw_step)*draw_step
            for _ in range(200):
                if y > top: break
                _,sy = self.to_screen(0,y)
                self.create_line(0,sy,w,sy,fill=GRID)
                self.create_text(6,sy-8,text=f'{y:g}',fill='#60758a',anchor='w',font=('Consolas',8))
                y += draw_step
        ox,oy = self.to_screen(0,0)
        self.create_line(0,oy,w-14,oy,fill='#ad6980',width=2,arrow='last')
        self.create_line(ox,h,ox,14,fill='#6698ba',width=2,arrow='last')
        self.create_text(w-20,min(h-35,max(30,oy-14)),text='X',fill='#dc95a9',font=('Consolas',12,'bold'))
        self.create_text(min(w-25,max(35,ox+16)),20,text='Y',fill=BLUE,font=('Consolas',12,'bold'))
        self.create_text(ox+12,oy+13,text='0',fill=TEXT,font=('Consolas',9))
        points = self.editor.mode.points
        if self.app.flags['points'].get() and len(points)>1:
            self.create_line(*[v for p in points for v in self.to_screen(p.x,p.y)],fill='#6b7b8b',dash=(3,5))
        if self.app.flags['curves'].get():
            for i,p in enumerate(points[:-1]):
                path = [self.to_screen(*curve_position(points,i,k/36)) for k in range(37)]
                self.create_line(*[v for p in path for v in p],fill=CYAN,width=2.5)
        compiled = self.app.compiled
        if compiled is not None and not self.app.compiled_stale:
            stride = max(1,len(compiled.samples)//1400)
            if self.app.flags['samples'].get():
                origin=points[0]
                for sample in compiled.samples[::stride]:
                    x,y = self.to_screen(origin.x+sample.qx,origin.y+sample.qy)
                    self.create_oval(x-1.5,y-1.5,x+1.5,y+1.5,outline='',fill=ORANGE)
            if self.app.flags['perturbed'].get() and len(compiled.preview)>1:
                preview=compiled.preview[::stride]
                if preview[-1] != compiled.preview[-1]: preview.append(compiled.preview[-1])
                self.create_line(*[v for p in preview for v in self.to_screen(*p)],fill='#c09cf5',width=1.5,dash=(5,3))
            t = self.app.playhead.get()
            index = min(len(compiled.samples)-1,max(0,round(t/compiled.duration_ms*(len(compiled.samples)-1))))
            sample = compiled.samples[index]; x,y = self.to_screen(sample.x,sample.y)
            self.create_oval(x-8,y-8,x+8,y+8,outline='white',width=2)
        if self.app.flags['points'].get():
            # Handles of selected anchors only; their coordinates are always world values.
            for i in sorted(self.editor.selection):
                if i>=len(points): continue
                p=points[i]
                for field,segment in (('handle_out',i),('handle_in',i-1)):
                    if not 0<=segment<len(points)-1 or points[segment].curve != 'bezier': continue
                    a,b=segment_handles(points,segment)
                    hx,hy = a if field=='handle_out' else b
                    px,py=self.to_screen(p.x,p.y); sx,sy=self.to_screen(hx,hy)
                    self.create_line(px,py,sx,sy,fill=BLUE,dash=(3,3))
                    self.create_rectangle(sx-4,sy-4,sx+4,sy+4,fill=BG,outline=BLUE,width=2)
                    self.handles.append((i,field,sx,sy))
            for i,p in enumerate(points):
                x,y=self.to_screen(p.x,p.y); selected=i in self.editor.selection
                radius=max(13,7+len(str(i+1))*4)
                self.create_oval(x-radius,y-radius,x+radius,y+radius,fill=BLUE if selected else '#213949',
                                 outline='white' if selected else CYAN,width=2)
                self.create_text(x,y,text=str(i+1),fill='#0b1a28' if selected else '#eaf8f6',
                                 font=('Consolas',11,'bold'))
        if self.gesture and self.gesture['kind']=='box':
            x,y=self.gesture['start']; ex,ey=self.gesture['last']
            self.create_rectangle(x,y,ex,ey,outline=BLUE,fill='',dash=(4,3),width=2)
        self.create_rectangle(0,h-27,w,h,fill=BG,outline='')
        self.create_text(w-15,h-13,text=f'视图 ×{self.view.zoom:.2f}  |  世界单位',fill=TEXT,anchor='e',font=('Microsoft YaHei UI',9))

    def hover(self,event):
        x,y=self.to_world(event.x,event.y)
        self.app.coordinates.set(f'X {x:.2f}   Y {y:.2f}')

    def point_hit(self,x,y):
        if not self.app.flags['points'].get(): return None
        for i in reversed(range(len(self.editor.mode.points))):
            p=self.editor.mode.points[i]; sx,sy=self.to_screen(p.x,p.y)
            if math.hypot(x-sx,y-sy)<=13: return i
        return None

    def press(self,event):
        if not self.app.confirm_inspector_change():return
        self.focus_set(); self.app.stop_playback()
        self.app.cancel_compile()
        self.gesture=None
        if self.app.edit_mode.get()=='图片编辑':
            image=self.editor.mode.image
            if image and not image.locked and image.contains(*self.to_world(event.x,event.y)):
                self.editor.begin(); self.gesture=dict(kind='image',last=(event.x,event.y))
            return
        for i,field,x,y in reversed(self.handles):
            if abs(event.x-x)<7 and abs(event.y-y)<7:
                self.editor.begin(); self.gesture=dict(kind='handle',index=i,field=field,last=(event.x,event.y)); return
        hit=self.point_hit(event.x,event.y)
        additive=bool(event.state & 1)
        if hit is not None:
            if additive: self.editor.selection.add(hit)
            elif hit not in self.editor.selection: self.editor.selection={hit}
            self.editor.begin()
            self.gesture=dict(kind='point',last=(event.x,event.y),anchor=hit)
            self.app.refresh_selection(); self.redraw()
        else:
            if not additive: self.editor.selection.clear()
            self.gesture=dict(kind='box',start=(event.x,event.y),last=(event.x,event.y),additive=additive)
            self.app.refresh_selection(); self.redraw()

    def motion(self,event):
        if not self.gesture: return
        g=self.gesture
        try:
            if g['kind']=='box': g['last']=(event.x,event.y)
            elif g['kind']=='handle':
                x,y=self.to_world(event.x,event.y)
                if self.app.flags['snap'].get():
                    step=self.app.grid_spacing(); x,y=round(x/step)*step,round(y/step)*step
                setattr(self.editor.mode.points[g['index']],g['field'],(x,y))
                self.app.on_live_change()
            else:
                old=self.to_world(*g['last']); new=self.to_world(event.x,event.y)
                dx,dy=new[0]-old[0],new[1]-old[1]
                if g['kind']=='image': self.editor.move_image_live(dx,dy)
                else:
                    if self.app.flags['snap'].get():
                        p=self.editor.mode.points[g['anchor']]; step=self.app.grid_spacing()
                        dx,dy=round((p.x+dx)/step)*step-p.x,round((p.y+dy)/step)*step-p.y
                    self.editor.translate_live(dx,dy)
                g['last']=(event.x,event.y)
                self.app.on_live_change(geometry=g['kind']!='image')
            self.request_redraw()
        except ValueError as error:
            self.cancel_gesture(); self.app.report_error(error)

    def release(self,event):
        if not self.gesture: return
        g=self.gesture; self.gesture=None
        try:
            if g['kind']=='box':
                self.editor.select_box((*self.to_world(*g['start']),*self.to_world(event.x,event.y)),g['additive'])
                self.app.refresh_selection()
            else:
                self.editor.finish(); self.app.after_edit(geometry=g['kind']!='image')
            self.redraw()
        except ValueError as error: self.app.report_error(error)
        finally:
            if self.app.compiled_stale: self.app.schedule_compile()

    def double_click(self,event):
        if self.app.edit_mode.get()=='轨迹编辑' and self.point_hit(event.x,event.y) is None:
            self.editor.cancel(); self.gesture=None
            x,y=self.to_world(event.x,event.y)
            self.app.edit(lambda:self.editor.add_point(x,y))

    def cancel_gesture(self):
        self.editor.cancel(); self.gesture=None
        self.app.after_edit()

    def pan_start(self,event):
        self.app.stop_playback(); self.gesture=dict(kind='pan',last=(event.x,event.y))
    def pan_move(self,event):
        if not self.gesture or self.gesture['kind']!='pan': return
        x,y=self.gesture['last']; self.view.pan(event.x-x,event.y-y)
        self.gesture['last']=(event.x,event.y); self.app.mark_view_changed(); self.request_redraw()
    def pan_end(self,event): self.gesture=None

    def wheel(self,event):
        factor=1.12 if event.delta>0 else 1/1.12
        if event.state & 0x20000 and self.app.edit_mode.get()=='图片编辑':
            image=self.editor.mode.image
            if image and not image.locked:
                self.app.edit(lambda:self.editor.set_image_pose(scale=min(100,max(.001,image.scale*factor))),geometry=False)
        else:
            self.view.zoom_at(factor,event.x,event.y,self.winfo_width(),self.winfo_height())
            self.app.mark_view_changed(); self.redraw()

    def dispose(self):
        if self._redraw_id: self.after_cancel(self._redraw_id); self._redraw_id=None
