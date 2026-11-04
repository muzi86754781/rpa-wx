"""Cancellable deliberate typing; temporary mistakes are corrected locally."""
import random
import time

_interval = .18
_corrections = True


def configure(interval_ms=180, corrections=True):
    global _interval, _corrections
    if type(interval_ms) is not int or not 80 <= interval_ms <= 800 or type(corrections) is not bool:
        raise ValueError('输入间隔应为 80–800 毫秒，纠错开关应为布尔值。')
    _interval, _corrections = interval_ms / 1000, corrections


def type_deliberately(text, stop, check, emit, backspace, delay=None, corrections=None, rng=None):
    rng = rng or random.SystemRandom()
    corrections = _corrections if corrections is None else corrections
    def pause(seconds):
        # Check focus and Escape during pauses, not only between characters.
        end = time.monotonic() + seconds
        while seconds > 0:
            check()
            if stop.wait(min(seconds, .05)):
                check()
                raise RuntimeError('操作已停止，输入框可能保留草稿，请检查。')
            seconds = end - time.monotonic()
    for index, char in enumerate(text):
        check()
        if corrections and delay is None and char.isalnum() and rng.random() < .025:
            emit('x')
            pause(rng.uniform(.18, .35))
            check()
            backspace()
            pause(rng.uniform(.1, .2))
        emit(char)
        if delay is not None:
            pause(max(0, delay))
        else:
            pause(rng.uniform(_interval * .75, _interval * 1.25))
            if char in '，。！？、；：,.!?;:\n':
                pause(rng.uniform(.25, .65))
            elif (index + 1) % 9 == 0:
                pause(rng.uniform(.2, .5))
