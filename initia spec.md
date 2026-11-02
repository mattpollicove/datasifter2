create specifications for a new project

LDAP V3 Browser that should run on Modern versions of  Windows, Mac, and Linux (KDE or Gnome)

it should have the following abilities
	create new connection
	modify existing connection
	delete a connection
	
Creating a connection should be based on a wizard that will first ask for username, password, URL/IP, port, LDAP or LDAPS There should be options to test both connectivity to the system and to test the username and password. If LDAPS if the certificate should be stored, and change the port value automatically. The application should read available base dns in a list to let the user choose where to connect to.

The program should be able to read entries, update entries, delete entries, do ModRDN
The program should be able to search entries based on an LDAP filter. There should be an assisted interface to create and check LDAP filters

tHE program should support import and export of LDIF files and validate LDIF files when being read or written.

What am I missing
What is incorrect 