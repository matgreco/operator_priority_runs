import logging
from pathlib import Path
import shutil
import subprocess

from downward import suites
from lab.experiment import Experiment, Run

import project
import report


class PlanningRun(Run):
    def __init__(self, experiment, planner, task, time_limit, memory_limit):
        super().__init__(experiment)
        self.add_resource(
            "domain", task.domain_file, "domain.pddl", symlink=False
        )
        self.add_resource(
            "problem", task.problem_file, "problem.pddl", symlink=False
        )
        self.add_command(
            "run-apptainer",
            [
                "{run_apptainer}",
                f"{{{planner.shortname}}}",
                "{domain}",
                "{problem}",
                "sas_plan",
                time_limit,
                memory_limit
            ]
        )

        self.set_property("algorithm", planner.shortname)
        self.set_property("planner_path", str(planner.image_path))
        self.set_property("domain", str(task.domain))
        self.set_property("problem", str(task.problem))
        self.set_property("id", [planner.shortname, str(task.domain), str(task.problem)])
        self.set_property("experiment_name", self.experiment.name)
        self.set_property("track", experiment.track)
        self.set_property("time_limit", time_limit)
        self.set_property("memory_limit", memory_limit)


class PlanningExperiment(Experiment):
    def __init__(self, track, time_limit, memory_limit, path=None, environment=None):
        super().__init__(path=path, environment=environment)
        self._tasks = {}
        self._planners = {}
        self.track = track
        self.time_limit = time_limit
        self.memory_limit = memory_limit

        self.add_step("build", self.build)
        self.add_step("start", self.start_runs)
        self.add_fetcher(name="fetch")
        if not project.running_on_cluster():
            self.add_step("remove-eval-dir", shutil.rmtree, self.eval_dir, ignore_errors=True)
            project.add_scp_step(self, "nsc", "/proj/dfsplan/users/x_jense/ipc2023-learning")
        reportfile = Path(self.eval_dir) / f"{self.name}.html"
        self.add_report(report.IPCPlanningReport(attributes=report.IPCPlanningReport.DEFAULT_ATTRIBUTES), outfile=reportfile)
        self.add_step(f"open-{reportfile.name}", subprocess.call, ["xdg-open", reportfile])
        
        self.add_parser(project.DIR / "planning-parser.py")
        self.add_parser(project.DIR / "runsolver-parser.py")
        self.add_resource("run_apptainer", project.DIR / "run-apptainer.sh")

    def add_domain(self, domain, domain_dir):
        if domain in self._tasks:
            logging.critical(f"Domain {domain} was already added")
        domain_file = domain_dir / "domain.pddl"
        tasks = []
        for problem_file in sorted(domain_dir.glob("*.pddl")):
            if problem_file.name == "domain.pddl":
                continue
            tasks.append(suites.Problem(domain, problem_file.name, problem_file, domain_file))
        self._tasks[domain] = tasks

    def add_planners(self, planners):
        for planner in planners:
            self.add_planner(planner)

    def add_planner(self, planner):
        if planner.shortname in self._planners:
            logging.critical(f"Planner with name '{planner.shortname}' added twice.")
        self.add_resource(planner.shortname, planner.image_path, symlink=True)
        self._planners[planner.shortname] = planner

    def build(self, **kwargs):
        if not self._planners:
            logging.critical("You must add at least one planner image.")

        # Convert suite to strings (see FastDownwardExperiment.build).
        serialized_suites = {
            str(domain): [str(task) for task in tasks]
            for domain, tasks in self._tasks.items()
        }
        self.set_property("suite", serialized_suites)
        self.set_property("images", list(self._planners.keys()))

        for planner in self._planners.values():
            for domain, tasks in sorted(self._tasks.items()):
                for task in tasks: 
                    self.add_run(PlanningRun(self, planner, task, self.time_limit, self.memory_limit))

        super().build(**kwargs)
