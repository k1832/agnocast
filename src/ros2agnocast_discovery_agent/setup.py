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
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    entry_points={
        'console_scripts': [
            'discovery_agent = ros2agnocast_discovery_agent.agent:main',
        ],
    },
)
