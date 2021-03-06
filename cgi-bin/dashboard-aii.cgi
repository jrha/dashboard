#!/usr/bin/perl -T
# ${license-info}
# ${developer-info
# ${author-info}
# ${build-info}

use strict;
use warnings;

use lib '/usr/lib/perl';
use CAF::Process;
use Config::Tiny;
use JSON::XS;
use Switch;
use CGI;
use IO::File;
use IO::Zlib;

# Configuration file to boot from HD. Following pxelinux convention
# it must be called default so if the <IpAddressInHex> link
# is missing the node will boot from the hard disk
my $boothd = "default";

# Read AII configuration
my $cfg = new Config::Tiny();
$cfg = $cfg->read('/etc/aii/aii-shellfe.conf');

# PXE Linux directory
my $pxelinux_dir = $cfg->{_}->{nbpdir};

# Profile prefix
my $profile_prefix = $cfg->{_}->{profile_prefix} ? $cfg->{_}->{profile_prefix} : '';

# Profile format
my $profile_format = $cfg->{_}->{profile_format};

# cdb url
my $cdburl = $cfg->{_}->{cdburl};

# Array of available pxelinux configurations
my @cfg;

# Profiles available
my (@profiles);

=pod
=item GetHexAddr($hostname):(HEXADDRESS,IPADDRESS)
Get the hostname or an IP address and return the IP address in
hex (as required by pxelinux)
=cut
sub GetHexAddr
{
    # The 4th field is an array of the IP address of this node
    my @all_address = (gethostbyname($_[0]))[4];
    return if ($#all_address < 0);

    # We unpack the IP address
    my @tmp_address = unpack('C4',$all_address[0]);

    return sprintf ("%02X%02X%02X%02X",@tmp_address), sprintf ("%u.%u.%u.%u",@tmp_address);
}

=pod
=item ReadProfile($hostname):hash
Read the profile of the given host
=cut
sub ReadProfile
{
    my ($hostname) = @_;

    $hostname =~ /^([\w\-\.]+)$/;
    $hostname = $1;

    my $filename = "$cdburl/$profile_prefix$hostname.$profile_format";

    if (!-f "$filename") {
        die("$filename doesn't exist !")
    }
    else {
        my $fh;
        if ($profile_format eq 'json.gz') {
            $fh =  new IO::Zlib;
        } elsif ($profile_format eq 'json') {
            $fh =  new IO::File;
        } else {
            die ("Can't handle profile format : $profile_format");
        }

        if($fh->open($filename, 'r')) {
            my $json_text = join('', <$fh>);
            $fh->close();
            return JSON::XS->new->decode($json_text);
        } else {
            die("Cannot open $filename");
        }
    }
}

=pod
=item Initialize():void
Find profiles and PXE Configuration
=cut
sub Initialize
{

    if ($cdburl =~ m/^file:\/\/(\S+)$/ ) {
        $cdburl = $1;
    }
    else {
      die("Working only with file:// protocol for cdb : $cdburl");
    }

    unless ($profile_format =~ m/json/ ) {
        die("Working only with json format for profiles : $profile_format");
    }

    $cdburl =~ s/^file:\/\///;

    @profiles = ();

    opendir(DIR,$cdburl) || die "failed to opendir $cdburl: $!";
    # find all profiles in directory
    push @profiles,map { s/\.$profile_format$//; s/^$profile_prefix//; $_ .=''; } sort(grep(/\.$profile_format$/, readdir(DIR)));
    closedir(DIR);

    opendir(DIR, $pxelinux_dir) || die "failed to opendir $pxelinux_dir: $!";
    # Load the configurations list
    @cfg = sort(grep(/(\.cfg$)|($boothd)/, readdir(DIR)));
    closedir(DIR);
}

=pod
=item GetHosts():void
Print hosts list
=cut
sub GetHosts
{
    my (@all, $k, $json);

    for $k (@profiles) {

        my $hostname = $k;
        my ($hexaddr, $dotaddr) = GetHexAddr($hostname);
        my $link = "$pxelinux_dir/$hexaddr";
        my $existing_cfg;

        if (-f $link) {
            my $config = readlink($link);
            $existing_cfg = $config ? $config : 'NO CONFIG';
        }
        else {
            $existing_cfg = 'NO CONFIG';
        }

        push @all ,{
        'hostname' => $hostname,
        'hexaddr' => $hexaddr,
        'dotaddr' => $dotaddr,
        'bootcfg' => $existing_cfg
        };
    }

    $json =  JSON::XS->new->pretty->encode({hosts => \@all, available_cfg => \@cfg});

    print "Content-type: application/json\n\n$json";
}

=pod
=item GetProfile($hostname):void
Print JSON profile of requested host
=cut
sub GetProfile
{
    my ($hostname) = @_;

    $hostname =~ /^([\w\-\.]+)$/;
    $hostname = $1;

    my $profile = ReadProfile($hostname);

    #Remove sensitive sections
    $profile->{'software'} = ();

    my $json =  JSON::XS->new->pretty->allow_nonref->encode($profile);

    print "Content-type: application/json\n\n$json";
}

=pod
=item Configure($action,$hostname):void
Call aii-shellfe actions
=cut
sub Configure
{
    my ($action, $hostname) = @_;

    $hostname =~ /^([\w\-\.]+)$/;
    $hostname = $1;

    $action =~ /^(configure|install|reinstall|boot)$/;
    $action = $1;

    $ENV{PATH}="/bin:/usr/bin:/sbin:/usr/bin:/usr/sbin";

    my @command = qw(/usr/bin/sudo /usr/sbin/aii-shellfe);

    if ($action eq 'reinstall') {
        push(@command, '--remove', '--configure', '--install');
    }
    else {
        push(@command, '--'.$action);
    }

    push(@command, $hostname);

    my $p = new CAF::Process(\@command);
    my $output = $p->output();

    print "Content-type: text/plain\n\n";

    if ($? eq 0) {
        print "$output";
    }
    else {
        print "Failed to $action $hostname using aii-shellfe : $?";
    }

}

=pod
=item GetValues($stats,$format):void
Return hosts overview.
=cut
sub GetValues
{
    my ($stats, $format) = @_;
    $stats =~ /^(.*)$/;
    $stats = $1;
    $format =~ /^(stats|overview)$/;
    $format = $1;

    my @fields = split '/' , $stats;

    my (%all, $json);

    for my $k (@profiles) {

        my $hostname = $k;
        $hostname =~ /^([\w\-\.]+)$/;
        $hostname = $1;

        my $profile = ReadProfile($hostname);

        for my $field(@fields) {

            my $value = '';
            switch($field) {
                case 'kernel' {
                    $value = $profile->{'system'}{'kernel'}{'version'};
                }
                case 'os' {
                    $value = $profile->{'system'}{'aii'}{'nbp'}{'pxelinux'}{'label'};
                }
                case 'location' {
                    $value = $profile->{'hardware'}{'location'};
                }
                case 'serialnumber' {
                  $value = $profile->{'hardware'}{'serialnumber'};
                }
                case 'macaddress' {
                    for my $key (sort keys %{$profile->{'hardware'}{'cards'}{'nic'}}) {
                        $value .= "$key : $profile->{'hardware'}{'cards'}{'nic'}{$key}{'hwaddr'}\n";
                    }
                }
                case 'ipaddress' {
                    for my $key (sort keys %{$profile->{'system'}{'network'}{'interfaces'}}) {
                        $value .= "$key : $profile->{'system'}{'network'}{'interfaces'}{$key}{'ip'}\n";
                    }
                }
                case 'ram' {
                    for my $i ( 0 .. $#{ $profile->{'hardware'}{'ram'} } ) {
                        $value += $profile->{'hardware'}{'ram'}[$i]{'size'};
                    }
                    $value .= " Mb";
                }
                case 'cores' {
                    for my $i ( 0 .. $#{ $profile->{'hardware'}{'cpu'} } ) {
                        $value += $profile->{'hardware'}{'cpu'}[$i]{'cores'};
                    }
                    $value .= " cores";
                }
                case 'hdcapacity' {
                    for my $key (sort keys %{$profile->{'hardware'}{'harddisks'}}) {
                        my $cap = $profile->{'hardware'}{'harddisks'}{$key}{'capacity'} / 1024;
                        $value .= "$key : $cap Gb\n";
                    }
                }
                case 'bootcfg' {
                    my ($hexaddr,$dotaddr) = GetHexAddr($hostname);
                    my $link = readlink("$pxelinux_dir/$hexaddr");

                    if ($link eq 'localboot.cfg') {
                        $value = 'boot';
                    }
                    elsif (!$link or $link eq '') {
                        $value = 'unconfigured';
                    }
                    else {
                        $value = 'install';
                    }
                }
            }

            if($format eq 'stats') {
                $all{$field}{$hostname} = $value;
            }
            elsif($format eq 'overview') {
                $all{$hostname}{$field} = $value;
            }
        }
    }

    $json = JSON::XS->new->pretty->encode(\%all);

    print "Content-type: application/json\n\n$json";
}

#########################################################################
# MAIN
#########################################################################

# Load required variables
&Initialize;

my $query = new CGI;

my $action = $query->param('action') ? $query->param('action') : 0;

my $requested_host = $query->param('hostname') ? $query->param('hostname') : 0;

my $host_valid = $requested_host ne '' and grep(/^$requested_host/,@profiles) ? 1 : 0 ;

my $option = $query->param('option') ? $query->param('option') : 0;

my $stats = $query->param('stats') ? $query->param('stats') : 0;

if ($action eq 'getHosts') { &GetHosts(); }
elsif ($action eq 'getProfile' and $host_valid) { &GetProfile($requested_host); }
elsif ($action eq 'configure' and $option and $host_valid) { &Configure($option, $requested_host); }
elsif ($action eq 'getStats' and $stats) { &GetValues($stats,'stats'); }
elsif ($action eq 'getOverview' and $stats) { &GetValues($stats,'overview'); }
