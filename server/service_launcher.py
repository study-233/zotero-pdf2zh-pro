"""Keep spawn re-entry free of HTTP service initialization."""


def main():
    import multiprocessing

    multiprocessing.freeze_support()
    from runtime_storage import configure_startup_storage

    configure_startup_storage(__file__)
    from server import main as serve

    return serve()


if __name__ == "__main__":
    main()
