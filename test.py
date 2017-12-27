if __name__ == '__main__':
    from aiopedia import AIOPedia
    from asyncio import get_event_loop
    loop = get_event_loop()
    aawait = loop.run_until_complete
    bo = aawait(AIOPedia('Elon Musk').page())
    print(bo.title)