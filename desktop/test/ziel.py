# Zielfenster für den Fernsteuerungs-Smoke-Test (fremdes X-Programm, nicht Agora): schreibt jeden Klick, jede Taste
# und jedes Rad-Ereignis als Zeile nach AGORA_ZIEL_LOG. Lage/Größe passen zu ZIEL in src/selbsttest-fernsteuerung.js.
import os
import tkinter as tk

LOG = os.environ.get('AGORA_ZIEL_LOG', '/tmp/ziel.log')
f = open(LOG, 'a', buffering=1, encoding='utf-8')
w = tk.Tk()
w.title('Zielfenster')
w.geometry('520x300+40+40')
w.configure(background='#203040')


def klick(e):
    f.write(f'klick {e.num} {e.x_root} {e.y_root}\n')
    w.focus_force()


w.bind('<ButtonPress-1>', klick)
w.bind('<ButtonPress-3>', klick)
w.bind('<Button-4>', lambda e: f.write('rad oben\n'))
w.bind('<Button-5>', lambda e: f.write('rad unten\n'))
w.bind('<MouseWheel>', lambda e: f.write(f'rad {"oben" if e.delta > 0 else "unten"}\n'))
w.bind('<KeyPress>', lambda e: f.write(f'taste {e.char if e.char and e.char.isprintable() else e.keysym} [{e.keysym}]\n'))
w.after(200, w.focus_force)
f.write('bereit\n')
w.mainloop()
