XyloSiteMonitor is a single-file python script which tests every
permutation of ways your website can be accessed:
- IPv4 or IPv6 
- TLS or not
- www or not 

Some permutations will result in a page loading. Others may result in
a redirect. All permutations need testing.

Websites to be tested are defined in a beautiful YAML format.

It produces a nice report and can pass the report to a local
mail-server if you want.

Here's an example: 

`
- name: HomePage
  expected string: Joseph Graham
  canonical address: https://www.xylon.me.uk/
  urls:
  - url: www.xylon.me.uk
    tests:
    - action: return string
      protocols:
        - TLS
    - action: redirect
      protocols:
        - no-TLS
  - url: xylon.me.uk
    tests:
    - action: redirect
      protocols:
        - TLS
        - no-TLS

Summary:
8 tests passed
0 tests failed
`

This translates into 8 checks and produces the following output: 
`
IPv4, does "www.xylon.me.uk" return string over "TLS"?
 Test Success!
IPv6, does "www.xylon.me.uk" return string over "TLS"?
 Test Success!
IPv4, does "www.xylon.me.uk" redirect over "no-TLS"?
 Test Success!
IPv6, does "www.xylon.me.uk" redirect over "no-TLS"?
 Test Success!
IPv4, does "xylon.me.uk" redirect over "TLS"?
 Test Success!
IPv6, does "xylon.me.uk" redirect over "TLS"?
 Test Success!
IPv4, does "xylon.me.uk" redirect over "no-TLS"?
 Test Success!
IPv6, does "xylon.me.uk" redirect over "no-TLS"?
 Test Success!
`

To monitor all 10 of my websites like this means 64 checks. To get
this many checks with UptimeRobot would require a Pro plan and it
wouldn't allow me to test IPv4 vs 6. Rival service Pingdom can do IPv4
or 6 but this many checks would require an "Advanced" plan for £48 GBP
per month!

Dependencies are:
- python3
- python3-yaml
- python3-pycurl

To find out the options please run:

`
./xylositemonitor.py --help
`

I use it as a cron job on a dedicated monitoring server: 
`
25 5 * * * /usr/local/bin/xylositemonitor.py --annotation 'SiteMonitor daily' --mailto joseph@xylon.me.uk
33 * * * * /usr/local/bin/xylositemonitor.py --annotation 'SiteMonitor hourly' --mailto joseph@xylon.me.uk --email-only-on-fail
`

This way I get an email every day, usually telling me all tests
passed. And there's also an hourly check which only sends an email on
error.
