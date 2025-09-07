import argparse

from .helper.logging import LOGGER
from axml.printer import ARSCPrinter, AXMLPrinter


def initAXMLParser():
    parser = argparse.ArgumentParser(
        prog='axml',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description='AXML Parser',
    )

    parser.add_argument('-i', '--input', type=str, help='input AXML file')

    parser.add_argument('-v', '--verbose', action='store_true', help='verbose')
    args = parser.parse_args()
    return args


def initARSCParser():
    parser = argparse.ArgumentParser(
        prog='arsc',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description='ARSC Parser',
    )

    parser.add_argument('-i', '--input', type=str, help='input ARSC file')

    parser.add_argument('-v', '--verbose', action='store_true', help='verbose')
    args = parser.parse_args()
    return args


def app_axml():
    arguments = initAXMLParser()

    if arguments.input:
        with open(arguments.input, 'rb') as fd:
            a = AXMLPrinter(fd.read())
            LOGGER.info(a.get_xml().decode('utf-8'))
    return 0


def app_arsc():
    arguments = initARSCParser()

    if arguments.input:
        with open(arguments.input, 'rb') as fd:
            a = ARSCPrinter(fd.read())
            LOGGER.info(a.get_xml().decode('utf-8'))
    return 0
