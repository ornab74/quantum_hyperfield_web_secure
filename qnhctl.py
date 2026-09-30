#!/usr/bin/env python3
from __future__ import annotations

import argparse
import getpass
import json
import os

from argon2 import PasswordHasher
from core import AsyncLoop, AsyncStore, CQSE, Hyperfield, QuantumEngine, simulate_dataset


def main():
    parser = argparse.ArgumentParser(description="Quantum Hyperfield admin utility")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("hash-password")
    sub.add_parser("init-db")

    demo = sub.add_parser("seed-demo")
    demo.add_argument("--count", type=int, default=240)

    dataset = sub.add_parser("simulate-dataset")
    dataset.add_argument("dataset_id")
    dataset.add_argument("--count", type=int, default=25)

    args = parser.parse_args()

    if args.command == "hash-password":
        password = getpass.getpass("Password: ")
        confirm = getpass.getpass("Confirm: ")
        if password != confirm:
            raise SystemExit("Passwords do not match")
        if len(password) < 16:
            raise SystemExit("Use at least 16 characters")
        print(PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2).hash(password))
        return

    loop = AsyncLoop()
    store = AsyncStore()

    if args.command == "init-db":
        loop.run(store.init())
        print("Database initialized")
    elif args.command == "seed-demo":
        loop.run(store.init())
        loop.run(store.seed_demo(args.count))
        print(f"Seeded {args.count} demo telemetry rows")
    elif args.command == "simulate-dataset":
        print(json.dumps(simulate_dataset(args.dataset_id, args.count), indent=2))


if __name__ == "__main__":
    main()
