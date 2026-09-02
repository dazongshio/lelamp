#!/usr/bin/env python3
import tkinter as tk


root = tk.Tk()
root.configure(background="white")
root.attributes("-fullscreen", True)
root.attributes("-topmost", True)
root.overrideredirect(True)
root.bind("<Escape>", lambda _event: root.destroy())
root.bind("q", lambda _event: root.destroy())

canvas = tk.Canvas(root, background="white", highlightthickness=0)
canvas.pack(fill="both", expand=True)

root.mainloop()
