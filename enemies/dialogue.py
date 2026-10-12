import random

PHASE_LINES = ["Enough games!", "You are stronger than you look.", "Now you see what I truly am."]


def boss_engage_line(attempt, main_damage, fallen):
    if attempt <= 1:
        if fallen:
            return f"My sibling, {fallen[-1]}, fell to your {main_damage}. I have prepared."
        return "You dare?"
    if attempt == 2:
        return "Back so soon? Brave. Or foolish."
    if attempt <= 4:
        return f"Attempt {attempt}. Do you count them, as I do?"
    return f"Again? Your {main_damage} will not save you this time."


def boss_phase_line(phase_index):
    return PHASE_LINES[min(max(phase_index - 1, 0), len(PHASE_LINES) - 1)]


def boss_death_taunt(main_damage):
    return random.choice([f"Your {main_damage} was never enough.",
                          "Rise. I am not finished with you.",
                          "Another one for the dust."])


def boss_last_words():
    return random.choice(["Impossible...", "The next one... will remember me...",
                          "You have... changed... everything."])