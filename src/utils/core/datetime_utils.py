import datetime

# Prepends the current date to the system prompt
def prepend_date_context(system_prompt: str) -> str:
    current_date = datetime.date.today().strftime('%A, %B %d, %Y')
    date_context = f"The current date is {current_date}. Use this date as the reference for any date-related reasoning in your answer.\n"
    return date_context + system_prompt
