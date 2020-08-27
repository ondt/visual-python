from __future__ import annotations

import atexit
import itertools
import sys
import termios
from dataclasses import dataclass
from itertools import zip_longest
from typing import Iterable, Iterator, List, NamedTuple, Optional

import readchar

from visual import ansi



# config
NUM_COLOR = ansi.red
TXT_COLOR = ansi.yellow | ansi.italic
OP_COLOR = ansi.green
FRAC_COLOR = ansi.reset
PAREN_COLOR = ansi.reset
FRAC_PADDING = 1

var = 10



def terminal_echo(enabled: bool):
	fd = sys.stdin.fileno()
	iflag, oflag, cflag, lflag, ispeed, ospeed, cc = termios.tcgetattr(fd)
	
	if enabled:
		lflag |= termios.ECHO
	else:
		lflag &= ~termios.ECHO
	
	new_attr = [iflag, oflag, cflag, lflag, ispeed, ospeed, cc]
	termios.tcsetattr(fd, termios.TCSANOW, new_attr)



atexit.register(terminal_echo, True)
terminal_echo(False)



def obj_index(iterable: Iterable, obj: object) -> int:
	"""The same as `list.index()`, but compares the actual objects using `is` instead of their values using `==`."""
	for index, item in enumerate(iterable):
		if item is obj:
			return index
	
	raise ValueError  # not found



def eprint(*args, **kwargs):
	print(*args, file=sys.stderr, **kwargs)
	sys.stderr.flush()



@dataclass
class ScreenOffset:
	row: int
	col: int



def cursor_string(off: ScreenOffset) -> str:
	off = off or ScreenOffset(0, 0)
	assert off.row >= 0
	assert off.col >= 0
	return f"\033[{off.row + 1};{off.col + 1}H"



def str_align(s: str, /, width: int) -> str:
	return f"{s:^{width}}"



def list_align(ls: List[str], /, width: int) -> List[str]:
	if width == 0:
		return []
	
	out = []
	out.extend(ls)
	
	left = False
	while len(out) < width:
		if left := not left:
			out.append("")
		else:
			out.insert(0, "")
	
	assert len(out) == width, f"{len(out)} != {width}"
	return out



def align_space(expr: Expression, width: int):
	expr_width = expr.width()
	if expr_width == 0:
		return width // 2
	else:
		return f"{'x' * expr_width :^{width}}".index("x")



class RenderOutput(NamedTuple):
	lines: List[str]
	colors: List[List[str]]  # maybe list of tuples would be better?
	baseline: int
	cursor: Optional[ScreenOffset]



class Expression:
	def children(self) -> List[Expression]:
		raise NotImplementedError
	
	def bfs_children(self) -> List[Expression]:
		return list(self._bfs_children())
	
	def _bfs_children(self) -> Iterator[Expression]:
		yield self
		for child in self.children():
			yield from child._bfs_children()
	
	def parentof(self, child: Expression) -> Expression:
		assert isinstance(child, Expression)
		for parent in self.bfs_children():
			for c in parent.children():
				if c is child:  # `child in parent.children()` uses `==` as well as `is`
					return parent
		
		raise ValueError("TODO")  # todo
	
	def width(self):
		lines = self.render().lines
		assert 1 == len(set(len(x) for x in lines)), "All lines must have the same length"
		return len(lines[0])
	
	def render(self) -> RenderOutput:
		raise NotImplementedError
	
	def simplify(self):
		pass  # todo: (abstract) join adjacent strings, flatten rows, remove dummy rows, add empty strings before/after fraction, etc
	
	def display(self, cursor: bool = True, colormap: bool = True, code: bool = True):  # todo: curses
		"""Render the expression onto the screen"""
		r = self.render()
		
		if not r.cursor:
			eprint(ansi.red("WARNING:"), "cursor is not present")
		
		output = []
		for line, color in zip(r.lines, r.colors):
			assert len(line) == len(color)
			colored_line = []
			
			for ch, pixel in zip(line, color):
				colored_line.append(f"{pixel}{ch}{ansi.reset}")
			
			output.append("".join(colored_line))
		
		if colormap:
			output.append("")
			for row in r.colors:
				line = []
				for color in row:
					line.append(f"{color or ansi.reset}▒{ansi.reset}")
				output.append("".join(line))
		
		if code:
			try:
				eval_result = eval(str(self))
			except Exception as e:
				eval_result = str(e)
			
			output.append("")
			output.append(f">>> {self}")
			output.append(f"{eval_result}")
		
		output.append("")  # newline at the end of the output
		
		if cursor:
			output.append(cursor_string(r.cursor))
		
		# clear, home, content
		print("\033[2J\033[H" + "\n".join(output), end="", flush=True)
	
	
	def press_key(self, key: str, root: Expression = None) -> bool:
		"""Only Text can have a cursor (pass the key on by default)"""
		root = root or self
		for child in self.children():
			accepted = child.press_key(key, root)
			if accepted:
				return True  # cursor could be moved multiple times if we wouldn't stop right there
		return False  # not accepted yet... (dead end)
	
	
	def __str__(self):
		raise NotImplementedError



class Text(Expression):
	def __init__(self, text: str):
		self.text: str = text
		self.cursor: Optional[ScreenOffset] = None
	
	def children(self) -> List[Expression]:
		return []
	
	def colorize(self) -> List[str]:  # list of colors
		output = []
		for char in self.text:
			if char.isalpha():
				output.append(TXT_COLOR)
			elif char.isdigit():
				output.append(NUM_COLOR)
			elif char in "+-*/=|&^@":
				output.append(OP_COLOR)
			else:
				output.append("")
		
		return output
	
	def render(self) -> RenderOutput:
		return RenderOutput([self.text], [self.colorize()], 0, self.cursor)
	
	
	def press_key(self, key: str, root: Expression = None) -> bool:
		if not self.cursor:
			return False  # we don't have the cursor, move on
		
		if not root and key in [readchar.key.UP, readchar.key.DOWN]:  # not root means self is the root
			eprint(ansi.red("cancelling vertical movement"))  # todo: jump to start/end
			return True  # keystroke accepted
		
		if key.isprintable():
			eprint(ansi.yellow(f"INSERT: '{key}'"))
			self.text = self.text[:self.cursor.col] + key + self.text[self.cursor.col:]
			self.cursor.col += 1
		
		if key == readchar.key.BACKSPACE:
			if self.cursor.col == 0:
				eprint(ansi.yellow("SPECIAL ACTION"))  # todo: remove fraction, etc
			else:
				eprint(ansi.yellow(f"REMOVE: '{self.text[self.cursor.col - 1]}'"))
				self.text = self.text[:self.cursor.col - 1] + self.text[self.cursor.col:]
				self.cursor.col -= 1
				assert self.cursor.col >= 0
		
		if key == readchar.key.LEFT:
			if self.cursor.col > 0:
				self.cursor.col -= 1
			else:
				self.press_key(readchar.key.UP, root)
		
		if key == readchar.key.RIGHT:
			if self.cursor.col < self.width():  # + one space at the end
				self.cursor.col += 1
			else:
				self.press_key(readchar.key.DOWN, root)
		
		if key == readchar.key.UP:
			eprint("GOING UP-----------------------")
			bfs_line = root.bfs_children()
			
			for ch in reversed(bfs_line[:obj_index(bfs_line, self)]):
				eprint("---->", ch.__class__.__name__, ansi.green(f"'{ch}'") if isinstance(ch, Text) else "")
			
			for expr in reversed(bfs_line[:obj_index(bfs_line, self)]):
				if isinstance(expr, Text):  # where we can jump to
					eprint("selected:", expr.__class__.__name__, ansi.green(f"'{expr}'"))
					self.cursor = None
					expr.cursor = ScreenOffset(0, expr.width())  # end of the text field
					break
			else:  # no break happened before
				eprint(ansi.red("WARNING:"), "ran out of targets (DOWN)")
		
		if key == readchar.key.DOWN:
			eprint("GOING DOWN---------------------")
			bfs_line = root.bfs_children()
			
			for ch in bfs_line[obj_index(bfs_line, self) + 1:]:
				eprint("---->", ch.__class__.__name__, ansi.green(f"'{ch}'") if isinstance(ch, Text) else "")
			
			for expr in bfs_line[obj_index(bfs_line, self) + 1:]:
				if isinstance(expr, Text):  # where we can jump to
					eprint("selected:", expr.__class__.__name__, ansi.green(f"'{expr}'"))
					self.cursor = None
					expr.cursor = ScreenOffset(0, 0)  # start of the text field
					break
			else:  # no break happened before
				eprint(ansi.red("WARNING:"), "ran out of targets (DOWN)")
		
		return True  # keystroke accepted
	
	def __str__(self):
		return self.text



class Row(Expression):
	def __init__(self, *items: Expression):
		self.items = list(items)
	
	def children(self) -> List[Expression]:
		return self.items
	
	def render(self) -> RenderOutput:
		widths = [x.width() for x in self.items]
		lines, colors, baselines, cursors = zip(*[x.render() for x in self.items])
		baseline = max(baselines)
		
		cursor = None
		w_so_far = 0
		for (l, c, b, w, cur) in zip(lines, colors, baselines, widths, cursors):
			for _ in range(baseline - b):
				l.insert(0, " " * w)  # baseline top padding
				c.insert(0, list_align([""], w))  # baseline top padding
			
			if cur:
				cursor = ScreenOffset(
					row=cur.row + baseline - b,
					col=cur.col + w_so_far,
				)
			
			w_so_far += w
		
		output_lines = []
		output_colors = []
		for index, (l, c) in enumerate(zip(zip_longest(*lines, fillvalue=""), zip_longest(*colors, fillvalue=[]))):
			l = [str_align(x, w) for x, w in zip(l, widths)]
			c = [list_align(x, w) for x, w in zip(c, widths)]
			output_lines.append("".join(l))
			output_colors.append(list(itertools.chain(*c)))
		
		return RenderOutput(output_lines, output_colors, baseline, cursor)
	
	def __str__(self):
		return "".join([str(x) for x in self.items])



class Fraction(Expression):
	def __init__(self, numerator: Expression, denominator: Expression):
		self.numerator = numerator
		self.denominator = denominator
	
	def children(self) -> List[Expression]:
		return [self.numerator, self.denominator]
	
	def render(self) -> RenderOutput:
		w = 2 * FRAC_PADDING + max(self.numerator.width(), self.denominator.width())
		n = self.numerator.render()
		d = self.denominator.render()
		baseline = len(n.lines)
		assert 1 == len(set(len(x) for x in n.lines)), "All lines must have the same length"
		assert 1 == len(set(len(x) for x in d.lines)), "All lines must have the same length"
		assert n.cursor is None or d.cursor is None, "At least one of cursors must be None"
		
		cursor = None
		if n.cursor:
			cursor = ScreenOffset(
				row=n.cursor.row,
				col=n.cursor.col + align_space(self.numerator, w),
			)
		
		if d.cursor:
			cursor = ScreenOffset(
				row=d.cursor.row + baseline + 1,
				col=d.cursor.col + align_space(self.denominator, w),
			)
		
		output = []
		output.extend([str_align(l, w) for l in n.lines])
		output.append("─" * w)
		output.extend([str_align(l, w) for l in d.lines])
		
		colors = []
		colors.extend([list_align(c, w) for c in n.colors])
		colors.append([FRAC_COLOR] * w)
		colors.extend([list_align(c, w) for c in d.colors])
		
		return RenderOutput(output, colors, baseline, cursor)
	
	
	def __str__(self):
		return f"(({self.numerator}) / ({self.denominator}))"



class Parenthesis(Expression):
	def __init__(self, expr: Expression):
		self.expr: Expression = expr
	
	def children(self) -> List[Expression]:
		return [self.expr]
	
	def render(self) -> RenderOutput:
		expr_lines, expr_colors, baseline, cursor = self.expr.render()
		# width = max(len(l) for l in expr_lines)
		width = self.expr.width()
		# baseline = math.floor(len(expr_lines) / 2)
		
		if len(expr_lines) == 1:
			return RenderOutput([f"({expr_lines[0]})"], [[PAREN_COLOR] + expr_colors[0] + [PAREN_COLOR]], 0, ScreenOffset(cursor.row, cursor.col + 1))
		else:
			output = []
			colors = []
			for index, (line, color) in enumerate(zip(expr_lines, expr_colors)):
				if index == 0:
					lparen, rparen = "⎛", "⎞"
				elif index < len(expr_lines) - 1:
					lparen, rparen = "⎜", "⎟"
				else:
					lparen, rparen = "⎝", "⎠"
				
				output.append(f"{lparen}{line:^{width}}{rparen}")
				colors.append([PAREN_COLOR] + list_align(color, width) + [PAREN_COLOR])
			
			return RenderOutput(output, colors, baseline, ScreenOffset(cursor.row, cursor.col + 1))
	
	def __str__(self):
		return f"({self.expr})"



object_with_cursor = Text("555")
object_with_cursor.cursor = ScreenOffset(0, 1)

expression = Row(
	# Parenthesis(
	Row(
		Text(""),
		Fraction(
			Text("1"),
			Text("2"),
		),
		Text(""),
	),
	# ),
	Text(" + "),
	Text("var"),
	Text(" + "),
	Row(
		Text(""),
		Fraction(
			Row(
				Text(""),
				Fraction(
					Text("44444"),
					object_with_cursor,
				),
				Text(""),
			),
			Text("6"),
		),
		Text(""),
	),
)

# expression = Row(
# 	Row(
# 		Text(""),
# 		Fraction(
# 			Text("11111"),
# 			object_with_cursor,
# 		),
# 		Text(""),
# 	),
# 	Text(" + var"),
# )
# expression = object_with_cursor


for ch in expression.bfs_children():
	eprint(ch.__class__.__name__, f"'{ch!r}'" if isinstance(ch, Text) else "")

while True:
	# print("\033[2J\033[H", end="", flush=True)  # clear, home
	expression.simplify()
	expression.display()
	# print(expression, end="", flush=True)
	
	key = readchar.readkey()
	eprint()
	eprint(f"key pressed: {ansi.blue}0x{key.encode('utf8').hex()}{ansi.reset} ({list(readchar.key.__dict__.keys())[list(readchar.key.__dict__.values()).index(key)] if key in readchar.key.__dict__.values() else key})")
	
	if key == readchar.key.CTRL_C:
		break
	
	expression.press_key(key)

expression.display(cursor=False)