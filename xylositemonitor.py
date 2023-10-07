#! /usr/bin/env python3

import sys
import os
import re
from io import BytesIO
import argparse
import time

import yaml
import pycurl  # pycurl is annoyingly low-level but the easier
               # "requests" module does not allow forcing IP version

from datetime import datetime, timedelta, timezone
import OpenSSL
import ssl

# Command line arguments.
parser = argparse.ArgumentParser(description='Tests websites.')
parser.add_argument('--sites-file', dest='sitesfile')
parser.add_argument('--mailto', dest='mailaddress')
parser.add_argument('--annotation', dest='annotation')
parser.add_argument('--email-only-on-fail', dest='emailonlyfail', action='store_true')

# parse
args = parser.parse_args()
emailonlyfail = args.emailonlyfail

# since the empty string is falsy we can set defaults with this "short-circuiting" method
mailto = args.mailaddress or False
sitesfile = args.sitesfile or "/etc/xylosites.yml"
annotation = args.annotation or "XyloSiteMonitor"

class HeaderException(Exception):
    """Problem parsing the headers."""
    pass

# don't even try to open sitesfile unless it's there
if not os.path.isfile(sitesfile):
    print('Initialisation Error! Cannot find sitesfile at "' + sitesfile +
          '"\nPlease place it here or specify location with --sites-file=')
    sys.exit()

with open(sitesfile, 'r') as stream:
    loaded = yaml.safe_load(stream)
    options = loaded['options']
    sites = loaded['sites']

    # specific options
    exweeks = options['cert expiry weeks']

def send_mail(subject, mail_body):
    """send the mail"""
    import smtplib
    from email.mime.text import MIMEText

    msg = MIMEText(mail_body)
    msg['Subject'] = annotation + ': ' + subject
    msg['To'] = mailto
    msg['From'] = "xylositemonitor"

    smtpcon = smtplib.SMTP('localhost')  # use host as mail relay
    smtpcon.send_message(msg)
    smtpcon.quit()  # close connection


def header_function(headers, header_line):
    """We have to parse http headers manually becasue libcurl doesn't do it for us."""

    # HTTP standard specifies that headers are encoded in iso-8859-1.
    header_line = header_line.decode('iso-8859-1')

    # Header lines include the first status line (HTTP/1.x ...).
    if re.search(r'\AHTTP/[123456789]', header_line[:6]):
        # get status code
        status = re.search(r'[0123456789]{3}', header_line).group(0)
        headers['status'] = status
        return

    # We are going to ignore all lines that don't have a colon in them.
    # This will botch headers that are split on multiple lines...
    if ':' not in header_line:
        return

    # Break the header line into header name and value.
    hname, value = header_line.split(':', 1)

    # Remove whitespace that may be present.
    # Header lines include the trailing newline, and there may be whitespace
    # around the colon.
    hname = hname.strip()
    value = value.strip()

    # Header names are case insensitive.
    # Lowercase name here.
    hname = hname.lower()

    # Now we can actually record the header name and value.
    # Note: this only works when headers are not duplicated, see below.
    headers[hname] = value

BCOLORS = {
    "HEADER": '\033[95m',
    "OKBLUE": '\033[94m',
    "OKGREEN": '\033[92m',
    "WARNING": '\033[93m',
    "FAIL": '\033[91m',
    "ENDC": '\033[0m',
    "BOLD": '\033[1m',
    "UNDERLINE": '\033[4m',
    }

def config_fail(message):
    if not mailto:
        print(BCOLORS["WARNING"] + '  Config Error! ' + message + BCOLORS["ENDC"])

    else:
        mail_body = '  Config Error! ' + message + "\n"

        send_mail('config error!', mail_body)

    sys.exit()

def test_fail(message):
    return {
        "success": False,
        "text_body": BCOLORS["FAIL"] + "  Test Fail! " + message + BCOLORS["ENDC"] + "\n",
        "mail_body": "  Test Fail! " + message + "\n"
    }


def test_success():
    return {
        "success": True,
        "text_body": BCOLORS["OKGREEN"] + " Test Success!" + BCOLORS["ENDC"] + "\n",
        "mail_body": " Test Success!" + "\n"
    }


mail_body = ""

def call_curl(prefix, url, curliptype):
    buffer = BytesIO()
    c = pycurl.Curl()
    c.setopt(c.URL, prefix + url)
    c.setopt(c.FOLLOWLOCATION, False)
    c.setopt(c.TIMEOUT, 8)
    c.setopt(c.ACCEPT_ENCODING, "")
    c.setopt(c.USERAGENT, "xylositemonitor")
    c.setopt(c.IPRESOLVE, curliptype)
    c.setopt(c.WRITEFUNCTION, buffer.write)

    # we give curl a function to call which modifies our variable
    headers = {}
    c.setopt(c.HEADERFUNCTION, lambda x: header_function(headers, x))

    # call curl
    c.perform()
    c.close()

    # Figure out what encoding was sent with the response, if any.
    # Check against lowercased header name.
    encoding = None
    if 'content-type' in headers:
        content_type = headers['content-type'].lower()
        match = re.search(r'charset=(\S+)', content_type)
        if match:
            encoding = match.group(1)
    if encoding is None:
        # Default encoding for HTML is iso-8859-1
        encoding = 'iso-8859-1'

    body = buffer.getvalue()
    responsebody = body.decode(encoding)

    if 'status' not in headers:
        raise HeaderException("Can't get HTTP response code!")

    return (headers, responsebody)

def perform_test(prefix, url, action, ex_string, can_address, curliptype):
    """
    we return a dictionary like
      {"success": True, "text_body": "blah", "mail_body": "blah"}
    """

    try:
        headers, responsebody = call_curl(prefix, url, curliptype)
    except (pycurl.error, HeaderException) as e:
        return test_fail(str(e))

    # There are three supported actions to test for
    # http success
    #     this just tests for 200 status
    # return string
    #     this checks for 200 and the contents of the page for an expected string
    # redirect
    #     this checks that the status is a redirect code to the specified URL
    match action:
        case "http success":
            if headers['status'] != "200":
                return test_fail("HTTP status is: " + headers['status'])
            else:
                return test_success()

        case "return string":
            # just check at least the status is 200 before even checking string
            if headers['status'] != "200":
                return test_fail("HTTP status is: " + headers['status'])

            # we need ex_string var for this test
            try:
                re.search('[a-zA-Z0-9]+', ex_string).group(0)  # this will error
                                                               # on both blank
                                                               # string and
                                                               # non-string
            except (TypeError, AttributeError):
                config_fail('"return string" check specified but ' +
                            '"expected string" is not defined!')

            # now we grep for the expected string in the response body
            if not ex_string in responsebody:
                return test_fail("Don't find expected string!")
            else:
                return test_success()

        case "redirect":
            if headers['status'][:1] != "3":
                return test_fail("Response code is not a redirect: " +headers['status'])

            if 'location' not in headers:
                return test_fail("Response code is a redirect but no Location header!")

            # we need can_address var for this test
            try:
                re.search('[a-zA-Z0-9]+', can_address).group(0) # this will
                                                                # error on both
                                                                # blank string
                                                                # and non-string
            except (TypeError, AttributeError):
                config_fail('"redirect" check specified but ' +
                            '"canonical address" is not defined!')

            # now we check redirect location
            if not headers['location'] == can_address:
                return test_fail("Redirect location is wrong: " + headers['location'])
            else:
                return test_success()

        case _:
            # if we got here it means we didn't recognise the action
            config_fail('action not recognised!')

def cert_test(url):
    """
    we return a dictionary like
      {"success": True, "text_body": "blah", "mail_body": "blah"}
    """

    # we need to get the domain from the url
    domain = url.split('/')[0]

    # If it's https we check the certificate date before doing anything else
    # note this doesn't care about ipv4 vs 6 as it connects by hostname
    cert=ssl.get_server_certificate((domain, 443))  # it takes a tuple
    x509 = OpenSSL.crypto.load_certificate(OpenSSL.crypto.FILETYPE_PEM, cert)
    timestamp = x509.get_notAfter().decode('utf-8')
    etime = datetime.strptime(timestamp, '%Y%m%d%H%M%S%z')

    # now to compare
    delta_weeks = timedelta(weeks=exweeks)
    now = datetime.now(timezone.utc)

    if etime - now < delta_weeks:
        result = test_fail("certificate expires in " + etime.date().isoformat())
    else:
        result = test_success()

    # prepend test description
    prepend = f'does "{domain}" have at-least {exweeks} weeks before cert expiry?'
    result["mail_body"] = prepend + "\n" + result["mail_body"]
    result["text_body"] = prepend + "\n" + result["text_body"]

    return result

def test_summary(protocol, url, action, ex_string, can_address, curliptype, ipver):
    """since the perform_test function has multiple exit-points, we need to
    collect it's output and summarize what it means"""

    if protocol == "TLS":
        prefix = "https://"
    elif protocol == "no-TLS":
        prefix = "http://"

    # here we actually run the tests
    result = perform_test(prefix, url, action, ex_string, can_address,
                          curliptype)

    # prepend test description
    prepend = f'{ipver}, does "{url}" {action} over "{protocol}"?'
    result["mail_body"] = prepend + "\n" + result["mail_body"]
    result["text_body"] = prepend + "\n" + result["text_body"]

    return result

def test_site(site):
    """
    we return a dictionary like
      {"name": "Site Name", "success_count": 4, "fail_count": 2, "tests": []}
    """

    # get method allows us to have default value
    ex_string = site.get("expected string", "")
    can_address = site.get("canonical address", "")
    testipv4 = site.get("ipv4", True)
    testipv6 = site.get("ipv6", True)

    buildme = {"name": site["name"], "tests": []}

    # rather than using four levels of nested loop, we are flattening it using a
    # mind-boggling list comprehension instead
    urls_flattened = [
        {
            "url": urldef["url"],
            "action": test["action"],
            "protocol": protocol
        }
        for urldef in site["urls"]
        for test in urldef["tests"]
        for protocol in test["protocols"]
    ]

    for test in urls_flattened:
        # python doesn't have destructuring so do it like gorilla
        url, action, protocol = test["url"], test["action"], test["protocol"]

        if url[:7] == "http://" or url[:8] == "https://":
            config_fail('Do not specify protocol in url.')

        if not protocol in ("TLS", "no-TLS"):
            config_fail('Supported protocols are "TLS" and "no-TLS".')

        if exweeks > 0 and protocol == "TLS":
            # do an extra test
            buildme["tests"] += [cert_test(url)]

        if testipv4:
            buildme["tests"] += [test_summary(protocol, url, action, ex_string,
                                              can_address, pycurl.IPRESOLVE_V4,
                                              "IPv4")]

        if testipv6:
            buildme["tests"] += [test_summary(protocol, url, action, ex_string,
                                              can_address, pycurl.IPRESOLVE_V6,
                                              "IPv6")]

    buildme["success_count"] = [test["success"] for test in buildme["tests"]].count(True)
    buildme["fail_count"]    = [test["success"] for test in buildme["tests"]].count(False)

    return buildme

def check_result(site):
    """if the site has any failed tests, re-test it"""
    if site["fail_count"] == 0:
        return site
    else:
        # here we need to get the original data out of global variable "sites"
        # to test it again
        return test_site([x for x in sites if x["name"] == site["name"]][0])

# this is a list of dicts
siteresults = [test_site(site) for site in sites]

# any that failed will be re-tested
restest_total = len([x for x in siteresults if x["fail_count"] != 0])

if restest_total > 0:
    time.sleep(10)
    siteresults = [check_result(site) for site in siteresults]

# sort the sites based on success
siteresultssorted = sorted(siteresults, key=lambda x: x["fail_count"], reverse=True)

success_total = sum([site["success_count"] for site in siteresults])
fail_total    = sum([site["fail_count"]    for site in siteresults])

if not mailto:
    for site in siteresultssorted:
        print("_" + site["name"] + "_")
        print("")

        for test in site["tests"]:
            print(test["text_body"])

        print("")

    print("")
    print("Summary:")
    print(str(success_total) + " tests passed")
    print(str(fail_total) + " tests failed")
    print(str(restest_total) + " sites re-tested")

else:
    for site in siteresultssorted:
        mail_body += "_" + site["name"] + "_\n\n"

        for test in site["tests"]:
            mail_body += test["mail_body"] + "\n"

        mail_body += "\n"

    mail_body += "\n"
    mail_body += "Summary:\n"
    mail_body += str(success_total) + " tests passed\n"
    mail_body += str(fail_total) + " tests failed\n"
    mail_body += str(restest_total) + " sites re-tested\n"

    # we've got our mail body, now we just need to work out what our subject is
    if fail_total > 0:
        send_mail(str(fail_total) + ' failing tests!', mail_body)
    else:
        if not emailonlyfail:
            send_mail("all " + str(success_total) + " tests passed", mail_body)
