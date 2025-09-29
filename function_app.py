import logging
import azure.functions as func
from main import main_function
import os

run_on_startup_environment_variable = os.getenv("run_on_startup_environment_variable")
crons_timer = os.getenv("crons_timer")

app = func.FunctionApp()

@app.timer_trigger(schedule=crons_timer, arg_name="myTimer", run_on_startup=run_on_startup_environment_variable,
              use_monitor=False) 
def timer_trigger(myTimer: func.TimerRequest) -> None:
    if myTimer.past_due:
        logging.info('The timer is past due!')
    print("This is working")

    main_function()

    print("---Executed pilates-bot main function---")


    logging.info('Python timer trigger function executed.')

