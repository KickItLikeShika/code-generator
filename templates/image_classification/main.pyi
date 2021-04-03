{% block imports %}
import logging
from argparse import ArgumentParser
from datetime import datetime
from pathlib import Path
from typing import Any

import ignite.distributed as idist
from datasets import get_data_loaders, get_datasets
from ignite.engine import create_supervised_evaluator, create_supervised_trainer
from ignite.engine.events import Events
from ignite.metrics import Accuracy, Loss
from ignite.utils import manual_seed, setup_logger
from utils import (
    get_default_parser,
    initialize,
    log_metrics,
    setup_common_handlers,
    setup_exp_logging,
)

{% endblock %}


{% block run %}
def run(local_rank: int, config: Any, *args: Any, **kwags: Any):

    # -----------------------------
    # datasets and dataloaders
    # -----------------------------
    {% block datasets_and_dataloaders %}
    train_dataset, eval_dataset = get_datasets(config.data_path)
    train_dataloader, eval_dataloader = get_data_loaders(
        train_dataset=train_dataset,
        eval_dataset=eval_dataset,
        train_batch_size=config.train_batch_size,
        eval_batch_size=config.eval_batch_size,
        num_workers=config.num_workers,
    )
    {% endblock %}

    # ------------------------------------------
    # model, optimizer, loss function, device
    # ------------------------------------------
    {% block model_optimizer_loss %}
    device, model, optimizer, loss_fn = initialize(config)
    {% endblock %}

    # ----------------------
    # train / eval engine
    # ----------------------
    {% block engines %}
    train_engine = create_supervised_trainer(
        model=model,
        optimizer=optimizer,
        loss_fn=loss_fn,
        device=device,
        output_transform=lambda x, y, y_pred, loss: {'train_loss': loss.item()},
    )
    metrics = {
        'eval_accuracy': Accuracy(device=device),
        'eval_loss': Loss(loss_fn=loss_fn, device=device)
    }
    eval_engine = create_supervised_evaluator(
        model=model,
        metrics=metrics,
        device=device,
    )
    {% endblock %}

    # ---------------
    # setup logging
    # ---------------
    {% block loggers %}
    name = f"bs{config.train_batch_size}-lr{config.lr}-{optimizer.__class__.__name__}"
    now = datetime.now().strftime("%Y%m%d-%X")
    train_engine.logger = setup_logger("trainer", level=config.verbose, filepath=config.filepath / f"{name}-{now}.log")
    eval_engine.logger = setup_logger("evaluator", level=config.verbose, filepath=config.filepath / f"{name}-{now}.log")
    {% endblock %}

    # -----------------------------------------
    # checkpoint and common training handlers
    # -----------------------------------------
    {% block eval_ckpt_common_training %}
    eval_ckpt_handler = setup_common_handlers(
        config=config,
        eval_engine=eval_engine,
        train_engine=train_engine,
        model=model,
        optimizer=optimizer
    )
    {% endblock %}

    # --------------------------------
    # setup common experiment loggers
    # --------------------------------
    {% block exp_loggers %}
    exp_logger = setup_exp_logging(
        config=config,
        eval_engine=eval_engine,
        train_engine=train_engine,
        optimizer=optimizer,
        name=name
    )
    {% endblock %}

    # ----------------------
    # engines log and run
    # ----------------------
    {% block engines_run_and_log %}
    {% block log_training_results %}
    @train_engine.on(Events.ITERATION_COMPLETED(every=config.log_train))
    def log_training_results(engine):
        train_engine.state.metrics = train_engine.state.output
        log_metrics(train_engine, "Train", device)
    {% endblock %}

    {% block run_eval_engine_and_log %}
    @train_engine.on(Events.EPOCH_COMPLETED(every=config.log_eval))
    def run_eval_engine_and_log(engine):
        eval_engine.run(
            eval_dataloader,
            max_epochs=config.eval_max_epochs,
            epoch_length=config.eval_epoch_length
        )
        log_metrics(eval_engine, "Eval", device)
    {% endblock %}

    train_engine.run(
        train_dataloader,
        max_epochs=config.train_max_epochs,
        epoch_length=config.train_epoch_length
    )
    {% endblock %}
{% endblock %}

{% block main_fn %}
def main():
    parser = ArgumentParser(parents=[get_default_parser()])
    config = parser.parse_args()
    manual_seed(config.seed)
    config.verbose = logging.INFO if config.verbose else logging.WARNING
    if config.filepath:
        path = Path(config.filepath)
        path.mkdir(parents=True, exist_ok=True)
        config.filepath = path
    with idist.Parallel(
        backend=idist.backend(),
        nproc_per_node=config.nproc_per_node,
        nnodes=config.nnodes,
        node_rank=config.node_rank,
        master_addr=config.master_addr,
        master_port=config.master_port
    ) as parallel:
        parallel.run(run, config=config)
{% endblock %}


{% block entrypoint %}
if __name__ == "__main__":
    main()
{% endblock %}