"""Keep spawn re-entry free of HTTP service initialization."""


def main():
    import multiprocessing

    multiprocessing.freeze_support()
    from server import main as serve

    return serve()


if __name__ == "__main__":
    main()
