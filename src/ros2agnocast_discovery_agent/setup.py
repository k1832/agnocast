from setuptools import find_packages, setup

package_name = 'ros2agnocast_discovery_agent'

setup(
    name=package_name,
    version='2.3.3',
    packages=find_packages(),
    data_files=[
        ('share/' + package_name, ['package.xml']),
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name + '/launch', ['launch/discovery_agent.launch.xml']),
        ('share/' + package_name + '/systemd', ['systemd/agnocast-discovery-agent.service']),
        # Install the runnable wrapper into the lib/<pkg>/ path where `ros2
        # run` looks for executables. ament_python in older releases doesn't
        # consistently move `console_scripts` here, so we do it explicitly.
        ('lib/' + package_name, ['scripts/discovery_agent']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
)
