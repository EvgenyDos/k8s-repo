#!/usr/bin/env python
'''
Author: Rich Wellum (richwellum@gmail.com)

This is a tool to build a running Kubernetes Cluster on a single Bare Metal
server or a VM running on a server.

Initial supported state is a Centos 7 VM.

Configuration is done with kubeadm.

This relies heavily on the OpenStack kolla-kubernetes project and in
particular the Bare Metal Deployment Guide:

https://docs.openstack.org/developer/kolla-kubernetes/deployment-guide.html

Inputs:

1. mgmt_int   : Name of the interface to be used for management operations
2. mgmt_ip    : IP Address of management interface
3. neutron_int: Name of the interface to be used for Neutron operations

TODO:

1. Will need a blueprint if adding this to community
2. Make it work on a Ubuntu host or vm
3. Pythonify some of these run_shells
4. Potentially build a docker container or VM to run this on

Dependencies:

Install pip:
  curl "https://bootstrap.pypa.io/get-pip.py" -o "get-pip.py"
  sudo python get-pip.py

Install psutil:
  sudo yum install gcc python-devel
  sudo pip install psutil
'''

from __future__ import print_function
import sys
import os
import time
import subprocess
import argparse
from argparse import RawDescriptionHelpFormatter
import logging
import psutil
import re
import tarfile

__author__ = 'Rich Wellum'
__copyright__ = 'Copyright 2017, Rich Wellum'
__license__ = ''
__version__ = '1.0.0'
__maintainer__ = 'Rich Wellum'
__email__ = 'rwellum@gmail.com'

TIMEOUT = 600

logger = logging.getLogger(__name__)


def set_logging():
    '''
    Set basic logging format.
    '''
    FORMAT = "[%(asctime)s.%(msecs)03d %(levelname)8s: %(funcName)20s:%(lineno)s] %(message)s"
    logging.basicConfig(format=FORMAT, datefmt="%H:%M:%S")


class AbortScriptException(Exception):
    '''Abort the script and clean up before exiting.'''


def parse_args():
    '''Parse sys.argv and return args'''
    parser = argparse.ArgumentParser(
        formatter_class=RawDescriptionHelpFormatter,
        description='A tool to create a working Kubernetes Cluster on Bare Metal or a VM.',
        epilog='E.g.: k8s.py eth0 10.240.43.250 eth1 -c -v -kv 1.6.2 -hv 2.4.2\n')
    parser.add_argument('MGMT_INT',
                        help='Management Interface, E.g: eth0')
    parser.add_argument('MGMT_IP',
                        help='Management Interface IP Address, E.g: 10.240.83.111')
    parser.add_argument('NEUTRON_INT',
                        help='Neutron Interface, E.g: eth1')
    parser.add_argument('-hv', '--helm_version', type=str, default='2.4.1',
                        help='Specify a different helm version to the default(2.4.1)')
    parser.add_argument('-kv', '--k8s_version', type=str, default='1.6.3',
                        help='Specify a different ansible version to the default(1.6.3)')
    parser.add_argument('-av', '--ansible_version', type=str, default='2.2.0.0',
                        help='Specify a different k8s version to the default(2.2.0.0)')
    parser.add_argument('-jv', '--jinja2_version', type=str, default='2.8.1',
                        help='Specify a different jinja2 version to the default(2.8.1)')
    parser.add_argument('-c', '--cleanup', action='store_true',
                        help='Cleanup existing Kubernetes cluster before creating a new one')
    parser.add_argument('-k8s', '--kubernetes', action='store_true',
                        help='Stop after bringing up kubernetes, do not install OpenStack')
    parser.add_argument('-n', '--nslookup', action='store_true',
                        help='Pause for the user to manually test nslookup in kubernetes cluster')
    # parser.add_argument('-l,', '--cloud', type=int, default=3,
    #                     help='optionally change cloud network config files from default(3)')
    parser.add_argument('-v', '--verbose', action='store_const',
                        const=logging.DEBUG, default=logging.INFO,
                        help='turn on verbose messages')

    return parser.parse_args()


def run_shell(cmd):
    '''Run a shell command and return the output
    Print the output if debug is enabled
    Not using logger.debug as a bit noisy for this info'''
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, shell=True)
    out = p.stdout.read()
    # logger.debug(out)
    if DEBUG == 10:  # Hack - debug enabled
        print(out)
    return(out)


def untar(fname):
    '''Untar a tarred and compressed file'''
    if (fname.endswith("tar.gz")):
        tar = tarfile.open(fname, "r:gz")
        tar.extractall()
        tar.close()
    elif (fname.endswith("tar")):
        tar = tarfile.open(fname, "r:")
        tar.extractall()
        tar.close()


def pause_to_debug(str):
    '''Pause the script for manual debugging of the VM before continuing.'''
    print('Pause: "%s"' % str)
    raw_input('Press Enter to continue')


def curl(*args):
    '''Use curl to retrieve a file from a URI'''
    curl_path = '/usr/bin/curl'
    curl_list = [curl_path]
    for arg in args:
        curl_list.append(arg)
    curl_result = subprocess.Popen(
        curl_list,
        stderr=subprocess.PIPE,
        stdout=subprocess.PIPE).communicate()[0]
    return curl_result


def k8s_create_repo():
    '''Create a k8s repository file'''
    name = './kubernetes.repo'
    repo = '/etc/yum.repos.d/kubernetes.repo'
    with open(name, "w") as w:
        w.write("""\
[kubernetes]
name=Kubernetes
baseurl=http://yum.kubernetes.io/repos/kubernetes-el7-x86_64
enabled=1
gpgcheck=0
repo_gpgcheck=1
gpgkey=https://packages.cloud.google.com/yum/doc/yum-key.gpg
       https://packages.cloud.google.com/yum/doc/rpm-package-key.gpg
""")
    run_shell('sudo mv ./kubernetes.repo %s' % repo)


def k8s_wait_for_kube_system():
    '''Wait for basic k8s to come up'''

    TIMEOUT = 350  # Give k8s 350s to come up
    RETRY_INTERVAL = 10
    elapsed_time = 0

    print('\nKubernetes - Wait for basic Kubernetes (6 pods) infrastructure')
    while True:
        pod_status = run_shell('kubectl get pods -n kube-system --no-headers')
        nlines = len(pod_status.splitlines())
        if nlines == 6:
            print('Kubernetes - All pods %s/6 are started, continuing' % nlines)
            run_shell('kubectl get pods -n kube-system')
            break
        elif elapsed_time < TIMEOUT:
            if nlines < 0:
                cnt = 0
            else:
                cnt = nlines

            if elapsed_time is not 0:
                print('Kubernetes - Pod status after %d seconds, pods up %s:6 - '
                      'sleep %d seconds and retry'
                      % (elapsed_time, cnt, RETRY_INTERVAL))
            time.sleep(RETRY_INTERVAL)
            elapsed_time = elapsed_time + RETRY_INTERVAL
            continue
        else:
            # Dump verbose output in case it helps...
            print(pod_status)
            raise AbortScriptException(
                "Kubernetes - did not come up after {0} seconds!"
                .format(elapsed_time))


def k8s_wait_for_running(number, namespace):
    """Wait for k8s pods to be in running status
    number is the minimum number of 'Running' pods expected"""

    TIMEOUT = 1000  # Give k8s 1000s to come up
    RETRY_INTERVAL = 10

    print("Kubernetes - Wait for %s '%s' pods to be in Running state:"
          % (number, namespace))
    elapsed_time = 0
    while True:
        running = run_shell('kubectl get pods -n %s | grep "Running" | wc -l' % namespace)

        if int(running) >= number:
            print('Kubernetes - All Running pods %s:%s' % (int(running), number))
            run_shell('kubectl get pods -n %s' % namespace)
            break
        elif elapsed_time < TIMEOUT:
            print('Kubernetes - Running pods %s:%s - sleep %d seconds and retry'
                  % (int(running), number, RETRY_INTERVAL))
            time.sleep(RETRY_INTERVAL)
            elapsed_time = elapsed_time + RETRY_INTERVAL
            continue
        else:
            # Dump verbose output in case it helps...
            print(int(running))
            raise AbortScriptException(
                "Kubernetes did not come up after {0} seconds!"
                .format(elapsed_time))


def k8s_wait_for_running_negate():
    '''Query get pods until only state is Running'''

    TIMEOUT = 1000  # Give k8s 1000s to come up
    RETRY_INTERVAL = 2

    print("Kubernetes - Wait for all pods to be in Running state:")
    elapsed_time = 0
    prev_not_running = 0
    while True:
        not_running = run_shell(
            'kubectl get pods --no-headers --all-namespaces | grep -v "Running" | wc -l')

        if int(not_running) != 0:
            if prev_not_running != not_running:
                print('Kubernetes - %s pod(s) are not in Running state' % int(not_running))
            time.sleep(RETRY_INTERVAL)
            elapsed_time = elapsed_time + RETRY_INTERVAL
            prev_not_running = not_running
            continue
        else:
            print('Kubernetes - All pods are in Running state')
            break

        if elapsed_time > TIMEOUT:
            # Dump verbose output in case it helps...
            print(int(not_running))
            raise AbortScriptException(
                "Kubernetes did not come up after {0} 1econds!"
                .format(elapsed_time))
            sys.exit(1)


def k8s_install_tools():
    '''Basic tools needed for first pass'''
    print('Kolla - Install repos needed for kolla packaging')
    run_shell('sudo yum install -y epel-release')
    run_shell('sudo yum install -y python-pip')
    run_shell('sudo pip install -U pip')
    run_shell('sudo yum install -y ansible python-devel git gcc')


def k8s_setup_ntp():
    '''Setup NTP - this caused issues when doing it on a VM'''
    run_shell('sudo yum install -y ntp')
    run_shell('sudo systemctl enable ntpd.service')
    run_shell('sudo systemctl start ntpd.service')


def k8s_turn_things_off():
    '''Currently turn off SELinux and Firewall'''
    print('Kubernetes - Turn off SELinux')
    run_shell('sudo setenforce 0')
    run_shell('sudo sed -i s/enforcing/permissive/g /etc/selinux/config')

    print('Kubernetes - Turn off Firewalld if running')
    PROCNAME = 'firewalld'
    for proc in psutil.process_iter():
        if PROCNAME in proc.name():
            print('Found %s, Stopping and Disabling firewalld' % proc.name())
            run_shell('sudo systemctl stop firewalld')
            run_shell('sudo systemctl disable firewalld')


def k8s_install_k8s(version):
    '''Necessary repo to install kubernetes and tools
    This is often broken and may need to be more programatic'''
    print('Kubernetes - Creating kubernetes repo')
    run_shell('sudo pip install --upgrade pip')
    k8s_create_repo()
    print('Kubernetes - Installing kubernetes packages')
    run_shell(
        'sudo yum install -y docker ebtables kubelet kubeadm-%s kubectl-%s \
        kubernetes-cni-%s' % (version, version, version))  # todo - cni version should be?
    if version == '1.6.3':
        print('Kubernetes - 1.6.3 workaround')
        # Workaround until kubectl 1.6.4 is available
        curl(
            '-L',
            'https://github.com/sbezverk/kubelet--45613/raw/master/kubelet.gz',
            '-o', '/tmp/kubelet.gz')
        run_shell('sudo gunzip -d /tmp/kubelet.gz')
        run_shell('sudo mv -f /tmp/kubelet /usr/bin/kubelet')
        run_shell('sudo chmod +x /usr/bin/kubelet')


def k8s_setup_dns():
    '''DNS services'''
    print('Kubernetes - Start docker and setup the DNS server with the service CIDR')
    run_shell('sudo systemctl enable docker')
    run_shell('sudo systemctl start docker')  # todo - test order
    run_shell('sudo cp /etc/systemd/system/kubelet.service.d/10-kubeadm.conf /tmp')
    run_shell('sudo chmod 777 /tmp/10-kubeadm.conf')
    run_shell('sudo sed -i s/10.96.0.10/10.3.3.10/g /tmp/10-kubeadm.conf')
    run_shell('sudo mv /tmp/10-kubeadm.conf /etc/systemd/system/kubelet.service.d/10-kubeadm.conf')


def k8s_reload_service_files():
    '''Service files where modified so bring them up again'''
    print('Kubernetes - Reload the hand-modified service files')
    run_shell('sudo systemctl daemon-reload')
    # run_shell('sudo systemctl start docker')
    # run_shell('sudo systemctl restart kubelet')


def k8s_start_kubelet():
    '''Start kubectl'''
    print('Kubernetes - Enable and start kubelet')
    run_shell('sudo systemctl enable kubelet')
    run_shell('sudo systemctl start kubelet')


def k8s_fix_iptables():
    '''Maybe Centos only but this needs to be changed to proceed'''
    reload_sysctl = False
    print('Kubernetes - Fix iptables')
    run_shell('sudo cp /etc/sysctl.conf /tmp')
    run_shell('sudo chmod 777 /tmp/sysctl.conf')

    with open('/tmp/sysctl.conf', 'r+') as myfile:
        contents = myfile.read()
        if not re.search('net.bridge.bridge-nf-call-ip6tables=1', contents):
            myfile.write('net.bridge.bridge-nf-call-ip6tables=1' + '\n')
            reload_sysctl = True
        if not re.search('net.bridge.bridge-nf-call-iptables=1', contents):
            myfile.write('net.bridge.bridge-nf-call-iptables=1' + '\n')
            reload_sysctl = True
    if reload_sysctl is True:
        run_shell('sudo mv /tmp/sysctl.conf /etc/sysctl.conf')
        run_shell('sudo sysctl -p')


def k8s_deploy_k8s():
    '''Start the kubernetes master'''
    print('Kubernetes - Deploying Kubernetes with kubeadm')
    run_shell('sudo kubeadm init --pod-network-cidr=10.1.0.0/16 \
    --service-cidr=10.3.3.0/24 --skip-preflight-checks')


def k8s_load_kubeadm_creds():
    '''This ensures the user gets output from 'kubectl get pods'''
    print('Kubernetes - Load kubeadm credentials into the system')
    print('Kubernetes - Note "kubectl get pods --all-namespaces" should work now')
    home = os.environ['HOME']
    kube = os.path.join(home, '.kube')
    config = os.path.join(kube, 'config')

    if not os.path.exists(kube):
        os.makedirs(kube)
    run_shell('sudo -H cp /etc/kubernetes/admin.conf %s' % config)
    run_shell('sudo chmod 777 %s' % kube)
    run_shell('sudo -H chown $(id -u):$(id -g) $HOME/.kube/config')


def k8s_deploy_canal_sdn():
    '''SDN/CNI Driver of choice is Canal'''
    print('Kubernetes - Create RBAC')
    answer = curl(
        '-L',
        'https://raw.githubusercontent.com/projectcalico/canal/master/k8s-install/1.6/rbac.yaml',
        '-o', '/tmp/rbac.yaml')
    logger.debug(answer)
    run_shell('kubectl create -f /tmp/rbac.yaml')

    print('Kubernetes - Deploy the Canal CNI driver')
    answer = curl(
        '-L',
        'https://raw.githubusercontent.com/projectcalico/canal/master/k8s-install/1.6/canal.yaml',
        '-o', '/tmp/canal.yaml')
    logger.debug(answer)
    run_shell('sudo chmod 777 /tmp/canal.yaml')
    run_shell('sudo sed -i s@192.168.0.0/16@10.1.0.0/16@ /tmp/canal.yaml')
    run_shell('sudo sed -i s@10.96.232.136@10.3.3.100@ /tmp/canal.yaml')
    run_shell('kubectl create -f /tmp/canal.yaml')


def k8s_schedule_master_node():
    '''Normally master node won't be happy - unless you do this step to
    make it an AOI deployment

    While the command says "taint" the "-" at the end is an "untaint"'''
    print('Kolla - Mark master node as schedulable')
    run_shell('kubectl taint nodes --all=true node-role.kubernetes.io/master:NoSchedule-')


def kolla_update_rbac():
    '''Override the default RBAC settings'''
    print('Kolla - Overide default RBAC settings')
    name = '/tmp/rbac'
    with open(name, "w") as w:
        w.write("""\
apiVersion: rbac.authorization.k8s.io/v1alpha1
kind: ClusterRoleBinding
metadata:
  name: cluster-admin
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: cluster-admin
subjects:
- kind: Group
  name: system:masters
- kind: Group
  name: system:authenticated
- kind: Group
  name: system:unauthenticated
""")
    run_shell('kubectl update -f /tmp/rbac')


def kolla_install_deploy_helm(version):
    '''Deploy helm binary'''
    print('Kolla - Install and deploy Helm version %s - Tiller pod' % version)
    url = 'https://storage.googleapis.com/kubernetes-helm/helm-v%s-linux-amd64.tar.gz' % version
    curl('-sSL', url, '-o', '/tmp/helm-v%s-linux-amd64.tar.gz' % version)
    untar('/tmp/helm-v%s-linux-amd64.tar.gz' % version)
    run_shell('sudo mv -f linux-amd64/helm /usr/local/bin/helm')
    run_shell('helm init')
    k8s_wait_for_running_negate()
    # Check for helm version
    # Todo - replace this to using json path to check for that field
    while True:
        out = run_shell('helm version | grep "%s" | wc -l' % version)
        if int(out) == 2:
            print('Kolla - Helm successfully installed')
            break
        else:
            time.sleep(3)
            continue


def k8s_cleanup(doit):
    '''Cleanup on Isle 9'''
    if doit is True:
        print('Kubernetes - Cleaning up existing Kubernetes Cluster')
        run_shell('sudo kubeadm reset')
        run_shell('sudo rm -rf /etc/kolla')
        run_shell('sudo rm -rf /etc/kubernetes')
        run_shell('sudo rm -rf /etc/kolla-kubernetes')
        run_shell('sudo rm -rf /var/lib/kolla*')


def kolla_install_repos():
    '''Installing the kolla repos
    For sanity I just delete a repo if already exists'''
    print('Kolla - Clone or update kolla-ansible')
    if os.path.exists('./kolla-ansible'):
        run_shell('sudo rm -rf ./kolla-ansible')
    run_shell('git clone http://github.com/openstack/kolla-ansible')

    print('Kolla - Clone or update kolla-kubernetes')
    if os.path.exists('./kolla-kubernetes'):
        run_shell('sudo rm -rf ./kolla-kubernetes')
    run_shell('git clone http://github.com/openstack/kolla-kubernetes')

    print('Kolla - Install kolla-ansible and kolla-kubernetes')
    run_shell('sudo pip install -U kolla-ansible/ kolla-kubernetes/')

    print('Kolla - Copy default Kolla configuration to /etc')
    run_shell('sudo cp -aR /usr/share/kolla-ansible/etc_examples/kolla /etc')

    print('Kolla - Copy default kolla-kubernetes configuration to /etc')
    run_shell('sudo cp -aR kolla-kubernetes/etc/kolla-kubernetes /etc')


def kolla_gen_passwords():
    '''Generate the Kolla Passwords'''
    print('Kolla - Generate default passwords via SPRNG')
    run_shell('sudo kolla-kubernetes-genpwd')


def kolla_create_namespace():
    '''Create a kolla namespace'''
    print('Kolla - Create a Kubernetes namespace to isolate this Kolla deployment')
    run_shell('kubectl create namespace kolla')


def k8s_label_nodes(node_list):
    '''Label the nodes according to the list passed in'''
    for node in node_list:
        print('Kolla - Label the AIO node as %s' % node)
        run_shell('kubectl label node $(hostname) %s=true' % node)


def k8s_check_exit(k8s_only):
    '''If the user only wants kubernetes and not kolla - stop here'''
    if k8s_only is True:
        print('Kubernetes Cluster is running and healthy and you do not wish to install kolla')
        sys.exit(1)


def kolla_modify_globals(MGMT_INT, MGMT_IP, NEUTRON_INT):
    '''Necessary additions and changes to the global.yml - which is based on
    the users inputs'''
    print('Kolla - Modify globals to setup network_interface and neutron_interface')
    run_shell("sudo sed -i 's/eth0/%s/g' /etc/kolla/globals.yml" % MGMT_INT)
    run_shell("sudo sed -i 's/#network_interface/network_interface/g' /etc/kolla/globals.yml")
    run_shell("sudo sed -i 's/10.10.10.254/%s/g' /etc/kolla/globals.yml" % MGMT_IP)
    run_shell("sudo sed -i 's/eth1/%s/g' /etc/kolla/globals.yml" % NEUTRON_INT)
    run_shell("sudo sed -i 's/#neutron_external_interface/neutron_external_interface/g' /etc/kolla/globals.yml")


def kolla_add_to_globals():
    '''Default section needed'''
    print('Kolla - Add default config to globals.yml')

    new = '/tmp/add'
    add_to = '/etc/kolla/globals.yml'

    with open(new, "w") as w:
        w.write("""\
kolla_install_type: "source"
tempest_image_alt_id: "{{ tempest_image_id }}"
tempest_flavor_ref_alt_id: "{{ tempest_flavor_ref_id }}"

neutron_plugin_agent: "openvswitch"
api_interface_address: 0.0.0.0
tunnel_interface_address: 0.0.0.0
orchestration_engine: KUBERNETES
memcached_servers: "memcached"
keystone_admin_url: "http://keystone-admin:35357/v3"
keystone_internal_url: "http://keystone-internal:5000/v3"
keystone_public_url: "http://keystone-public:5000/v3"
glance_registry_host: "glance-registry"
neutron_host: "neutron"
keystone_database_address: "mariadb"
glance_database_address: "mariadb"
nova_database_address: "mariadb"
nova_api_database_address: "mariadb"
neutron_database_address: "mariadb"
cinder_database_address: "mariadb"
ironic_database_address: "mariadb"
placement_database_address: "mariadb"
rabbitmq_servers: "rabbitmq"
openstack_logging_debug: "True"
enable_haproxy: "no"
enable_heat: "no"
enable_cinder: "yes"
enable_cinder_backend_lvm: "yes"
enable_cinder_backend_iscsi: "yes"
enable_cinder_backend_rbd: "no"
enable_ceph: "no"
enable_elasticsearch: "no"
enable_kibana: "no"
glance_backend_ceph: "no"
cinder_backend_ceph: "no"
nova_backend_ceph: "no"
""")
    run_shell('cat %s | sudo tee -a %s' % (new, add_to))


def kolla_enable_qemu():
    '''Some configurations need qemu'''
    print('Kolla - Enable qemu')
    run_shell('sudo mkdir -p /etc/kolla/config')

    new = '/tmp/add'
    add_to = '/etc/kolla/config/nova.conf'
    with open(new, "w") as w:
        w.write("""\
[libvirt]
virt_type = qemu
cpu_mode = none
""")
    run_shell('sudo mv %s %s' % (new, add_to))


def kolla_gen_configs(a_ver, j_ver):
    '''Generate the configs using Jinja2
    Some version meddling here until things are more stable'''
    print('Kolla - Generate the default configuration')
    # Seems to be the recommended ansible version
    run_shell('sudo pip install ansible==%s' % a_ver)
    # Standard jinja2 in Centos7(2.9.6) is broken
    run_shell('sudo pip install Jinja2==%s' % j_ver)
    # globals.yml is used when we run ansible to generate configs
    run_shell('cd kolla-kubernetes; sudo ansible-playbook -e \
    ansible_python_interpreter=/usr/bin/python -e \
    @/etc/kolla/globals.yml -e @/etc/kolla/passwords.yml \
    -e CONFIG_DIR=/etc/kolla ./ansible/site.yml; cd ..')


def kolla_gen_secrets():
    '''Generate Kubernetes secrets'''
    print('Kolla - Generate the Kubernetes secrets and register them with Kubernetes')
    run_shell('python ./kolla-kubernetes/tools/secret-generator.py create')


def kolla_create_config_maps():
    '''Generate the Kolla config map'''
    print('Kolla - Create and register the Kolla config maps')
    # kubectl create configmap mariadb --from-file=/etc/kolla/config.json
    # --from-file=/etc/kolla/galera.cnf
    # --from-file=/etc/kolla/wsrep-notify.sh -n kolla
    run_shell('kollakube res create configmap \
    mariadb keystone horizon rabbitmq memcached nova-api nova-conductor \
    nova-scheduler glance-api-haproxy glance-registry-haproxy glance-api \
    glance-registry neutron-server neutron-dhcp-agent neutron-l3-agent \
    neutron-metadata-agent neutron-openvswitch-agent openvswitch-db-server \
    openvswitch-vswitchd nova-libvirt nova-compute nova-consoleauth \
    nova-novncproxy nova-novncproxy-haproxy neutron-server-haproxy \
    nova-api-haproxy cinder-api cinder-api-haproxy cinder-backup \
    cinder-scheduler cinder-volume iscsid tgtd keepalived \
    placement-api placement-api-haproxy')


def kolla_resolve_workaround():
    '''Resolve.Conf workaround'''
    print('Kolla - Enable resolv.conf workaround')
    run_shell('./kolla-kubernetes/tools/setup-resolv-conf.sh kolla')


def kolla_build_micro_charts():
    '''Build all helm micro charts'''
    print('Kolla - Build all Helm microcharts, service charts, and metacharts')
    run_shell('kolla-kubernetes/tools/helm_build_all.sh .')


def kolla_verify_helm_images():
    '''Subjective but a useful check to see if enough helm charts were
    generated'''
    out = run_shell('ls | grep ".tgz" | wc -l')
    if int(out) > 180:
        print('Kolla - %s Helm images created' % int(out))
    else:
        print('Kolla - Error: only %s Helm images created' % int(out))
        sys.exit(1)


def kolla_create_and_run_cloud(MGMT_INT, MGMT_IP, NEUTRON_INT):
    '''Generate the cloud.yml file which works with the globals.yml
    file to define your cluster networking'''
    print('Kolla - Create and run cloud.yaml')
    cloud = '/tmp/cloud.yaml'
    with open(cloud, "w") as w:
        w.write("""\
global:
   kolla:
     all:
       image_tag: "4.0.0"
       kube_logger: false
       external_vip: "192.168.7.105"
       base_distro: "centos"
       install_type: "source"
       tunnel_interface: "docker0"
       resolve_conf_net_host_workaround: true
     keystone:
       all:
         admin_port_external: "true"
         dns_name: "192.168.7.105"
       public:
         all:
           port_external: "true"
     rabbitmq:
       all:
         cookie: 67
     glance:
       api:
         all:
           port_external: "true"
     cinder:
       api:
         all:
           port_external: "true"
       volume_lvm:
         all:
           element_name: cinder-volume
         daemonset:
           lvm_backends:
           - '192.168.7.105': 'cinder-volumes'
     ironic:
       conductor:
         daemonset:
           selector_key: "kolla_conductor"
     nova:
       placement_api:
         all:
           port_external: true
       novncproxy:
         all:
           port: 6080
           port_external: true
     openvwswitch:
       all:
         add_port: true
         ext_bridge_name: br-ex
         ext_interface_name: enp1s0f1
         setup_bridge: true
     horizon:
       all:
         port_external: true
""")
    # Note - external_vip should be an unused ip on your network
    run_shell('sudo sed -i s/192.168.7.105/%s/g %s' % (MGMT_IP, cloud))
    run_shell('sudo sed -i s/enp1s0f1/%s/g %s' % (NEUTRON_INT, cloud))
    run_shell('sudo sed -i s/docker0/%s/g %s' % (MGMT_INT, cloud))


def helm_install_service_chart(chart_list):
    '''helm install a list of service charts'''
    for chart in chart_list:
        print('Helm - Install service chart: %s' % chart)
        run_shell('helm install --debug kolla-kubernetes/helm/service/%s \
        --namespace kolla --name %s --values /tmp/cloud.yaml' % (chart, chart))
    k8s_wait_for_running_negate()


def helm_install_micro_service_chart(chart_list):
    '''helm install a list of micro service charts'''
    for chart in chart_list:
        print('Helm - Install service chart: %s' % chart)
        run_shell('helm install --debug kolla-kubernetes/helm/microservice/%s \
        --namespace kolla --name %s --values /tmp/cloud.yaml' % (chart, chart))
    k8s_wait_for_running_negate()


def sudo_timeout_off(state):
    '''Turn sudo timeout off or on'''
    # if state is True:
    # d = run_shell('sudo echo "Defaults timestamp_timeout=-1" >> /etc/sudoers')
    # sudo sh -c 'echo "Defaults timestamp_timeout=-1" >> /etc/sudoers'
    # print(d)
    # else:
    # d = run_shell('sudo sed -i "/Defaults timestamp_timeout=-1/d" /etc/sudoers')
    # print(d)


def kolla_create_keystone_admin():
    '''Using OpenStack

    If you were able to successfully reach the end of this guide and demo1 was
    successfully deployed, here is a fun list of things you can do
    with your new cluster.

    Access Horizon GUI

    1 Determine Horizon EXTERNAL IP Address:

    $ kubectl get svc horizon --namespace=kolla
    NAME      CLUSTER-IP   EXTERNAL-IP     PORT(S)   AGE
    horizon   10.3.3.237   10.240.43.175   80/TCP    1d

    2 Determine username and password from keystone:

    $ cat ~/keystonerc_admin | grep OS_USERNAME
    export OS_USERNAME=admin

    $ cat ~/keystonerc_admin | grep OS_PASSWORD
    export OS_PASSWORD=Sr6XMFXvbvxQCJ3Cib1xb0gZ3lOtBOD8FCxOcodU

    3 Run a browser that has access to your network, and access Horizon GUI with
    the EXTERNAL IP from Step 1, using the credentials from Step 2.'''

    run_shell('sudo rm -f ~/keystonerc_admin')
    run_shell('kolla-kubernetes/tools/build_local_admin_keystonerc.sh')
    address = run_shell("kubectl get svc horizon --namespace=kolla --no-headers | awk '{print $3}'")
    username = run_shell("cat ~/keystonerc_admin | grep OS_PASSWORD | awk '{print $2}'")
    password = run_shell("cat ~/keystonerc_admin | grep OS_USERNAME | awk '{print $2}'")
    print('To Access Horizon:')
    print('  Point your browser to: %s' % address)
    print('  %s' % username)
    print('  %s' % password)


def k8s_get_pods(namespace):
    '''Display all pods per namespace list'''
    for name in namespace:
        final = run_shell('kubectl get pods -n %s' % name)
        print('Kolla - Final Kolla Kubernetes Openstack pods for namespace %s:' % name)
        print(final)


def k8s_pause_to_check_nslookup(manual_check):
    '''Create a test pod and query nslookup against kubernetes
    Only seems to work in the default namespace

    Also handles the option to create a test pod manually like
    the deployment guide advises.'''
    print("Kubernetes - Test 'nslookup kubernetes'")
    name = './busybox.yaml'
    with open(name, "w") as w:
        w.write("""\
apiVersion: v1
kind: Pod
metadata:
  name: kolla-dns-test
spec:
  containers:
  - name: busybox
    image: busybox
    args:
    - sleep
    - "1000000"
""")
    run_shell('kubectl create -f %s' % name)
    k8s_wait_for_running_negate()
    print('Kubernetes - run dnslookup against a test pod')
    out = run_shell(
        'kubectl exec kolla-dns-test -- nslookup kubernetes | grep -i address | wc -l')
    logger.debug('Kolla DNS test output==%s' % out)
    if int(out) != 2:
        print("Kubernetes - Warning 'nslookup kubernetes ' failed. YMMV continuing")
        # sys.exit(1)
    else:
        print("'Kubernetes - nslookup kubernetes' worked - continuing")

    run_shell('Kubectl delete kolla-dns-test -n kube-system')

    if manual_check:
        print('Kubernetes - Run the following to create a pod to test kubernetes nslookup')
        print('Kubernetes - kubectl run -i -t $(uuidgen) --image=busybox --restart=Never')
        pause_to_debug('Check "nslookup kubernetes" now')


def main():
    '''Main function.'''
    args = parse_args()

    global DEBUG
    DEBUG = args.verbose

    print('Kubernetes - Management Int:%s, Management IP:%s, Neutron Int:%s' %
          (args.MGMT_INT, args.MGMT_IP, args.NEUTRON_INT))
    print('Helm version %s, Kubernetes version %s' %
          (args.helm_version, args.k8s_version))
    print('Ansible version %s, Jinja2 version %s' %
          (args.ansible_version, args.jinja2_version))
    time.sleep(5)

    set_logging()
    logger.setLevel(level=args.verbose)

    try:
        k8s_install_tools()
        k8s_cleanup(args.cleanup)
        # k8s_setup_ntp()

        # Bring up Kubernetes
        k8s_turn_things_off()
        k8s_install_k8s(args.k8s_version)
        k8s_setup_dns()
        k8s_reload_service_files()
        k8s_start_kubelet()
        k8s_fix_iptables()
        # k8s_reload_service_files()
        k8s_deploy_k8s()
        k8s_load_kubeadm_creds()
        # k8s_wait_for_kube_system()
        k8s_wait_for_running_negate()
        k8s_deploy_canal_sdn()
        k8s_wait_for_running_negate()
        k8s_schedule_master_node()
        k8s_pause_to_check_nslookup(args.nslookup)
        k8s_check_exit(args.kubernetes)

        # Start Kolla deployment
        kolla_update_rbac()
        kolla_install_deploy_helm(args.helm_version)
        kolla_install_repos()
        kolla_gen_passwords()
        kolla_create_namespace()

        # Label AOI as Compute and Controller nodes
        node_list = ['kolla_compute', 'kolla_controller']
        k8s_label_nodes(node_list)

        kolla_modify_globals(args.MGMT_INT, args.MGMT_IP, args.NEUTRON_INT)
        kolla_add_to_globals()
        kolla_enable_qemu()
        kolla_gen_configs(args.ansible_version, args.jinja2_version)
        kolla_gen_secrets()
        kolla_create_config_maps()
        kolla_resolve_workaround()
        kolla_build_micro_charts()
        kolla_verify_helm_images()
        kolla_create_and_run_cloud(args.MGMT_INT, args.MGMT_IP, args.NEUTRON_INT)

        # Install Helm charts
        chart_list = ['mariadb']
        helm_install_service_chart(chart_list)

        # Install remaining service level charts
        chart_list = ['rabbitmq', 'memcached', 'keystone', 'glance',
                      'cinder-control', 'horizon', 'openvswitch', 'neutron']
        helm_install_service_chart(chart_list)

        chart_list = ['nova-control', 'nova-compute']
        helm_install_service_chart(chart_list)

        chart_list = ['nova-cell0-create-db-job',
                      'nova-api-create-simple-cell-job']
        helm_install_micro_service_chart(chart_list)

        namespace_list = ['kube-system', 'kolla']
        k8s_get_pods(namespace_list)

        # todo: horizon is up, nova vm boots and ping google with good L3?
        kolla_create_keystone_admin()

    except Exception:
        print('Exception caught:')
        print(sys.exc_info())
        raise


if __name__ == '__main__':
    main()
